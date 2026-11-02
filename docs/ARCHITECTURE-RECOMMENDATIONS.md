# Brahma Evo — Architectural Recommendations, Design Decisions & Engineering Blueprint (Rev 2)

**Status:** Reviewed & Amended Annex to [`ARCHITECTURE-SPEC.md`](file:///c:/Users/Admin/Brahma-Ai-Evo/docs/ARCHITECTURE-SPEC.md)  
**Created:** 2026-11-01 · **Amended:** 2026-11-01 (post-verification review)  
**Baseline:** `main` @ `531b961` (744 passed / 1 skipped; `agent/` 387 passed)  
**Disposition:** Accepted and amended items have been merged into [`ARCHITECTURE-SPEC.md`](file:///c:/Users/Admin/Brahma-Ai-Evo/docs/ARCHITECTURE-SPEC.md) §11.3, §12, and §13. This document serves as the companion engineering rationale and records the full review disposition (§0).

---

## 0. Review Disposition (2026-11-01)

Every load-bearing claim and recommendation in Rev 1 was checked against the tree at `531b961`. The table below records what was accepted verbatim, what was amended to preserve ratified invariants, and what was rejected or deferred.

| Item | Rev 1 Proposal | Review Verdict | Final Disposition in Rev 2 & Spec |
|---|---|---|---|
| **O-1 (`N4`)** | Container-first verified confinement (`require_confined` + `sandbox doctor`). | **Accept with amendment** | WSL2 is not confinement (shares host FS/network). `sandbox doctor` must actively probe (write outside workspace, network egress, UID/namespace isolation) rather than trust a prefix label. `require_confined` must stay off unless a probe passes — the probe reports, it never writes policy. |
| **O-2 (`N5`)** | Keep mutation sequential; add `run_subagent_batch` for read-only parallel subagents. | **Accept with contract** | `tool_class == "read"` alone is not a boundary (`read_file` can read outside workspace). The batch contract must enforce: `tool_class == "read"` only, workspace-confined paths only, refuse `requires_approval=True`, exclude both `run_subagent` and `run_subagent_batch` from the **child's** advertised surface (`I-08`), and share parent `AgentRuntime` + `cancel_event`. |
| **O-3** | Split Security Phase 0 into two atomic batches (`Q0-A` Execution/Code, `Q0-B` Network/Storage). | **Accept as written** | Adopted in Spec §11.3 and §12 (`Q0-A` and `Q0-B`). |
| **O-4** | Re-base Agent Bridge onto `AgentRuntime` via `ADR-018` ("Bridge is a tool source, never a runtime"). | **Accept as written** | Adopted in Spec §11.3. |
| **O-5** | Freeze upstream port until security and boundary work land. | **Accept with amendment** | Freeze until `Q0-A` + `Q0-B` land (not blocked on `O-6` Phase 1). |
| **O-6** | Migrate `actions/*` into `ToolSpec` by capability class; retire `core/tool_exec.py` `elif` chain. | **Accept with amendment** | Use existing `TOOL_CLASSES` (`"read"`, `"write"`, `"exec"`, `"network"`, `"device"`, `"mcp"`, `"native"` in `agent/spec.py:30`). Drop unnecessary `is_async` (`actions/` has zero `async def` handlers; `core/tool_exec.py:131` already uses a worker thread) and `ui_feedback`. |
| **O-7** | Disable `agent_task` in `Q0-A`; delete legacy planner/executor files in `Q1`. | **Accept as written** | Adopted in Spec §11.3 and §12. |
| **O-8 (`1008`)** | 3-layer defense: `call_tool(...)` template output + structured search + reconnect auto-pinning. | **Accept Layers 1–2; amend Layer 3** | Pinning in `ToolIndex` is global (`entry.pinned`), so Layer 3 must pass a session-scoped `pinned_extra` parameter into `ToolIndex.resolve(intent, budget=budget(), pinned_extra=...)` via `build_session_payload`, never replay the user turn (`last_intent` is for retrieval only; session resumption restores history), and delete the unbudgeted `mcp_Synapse`/`cdp` promotion block at `core/tool_bridge.py:386–398` (which currently violates `I-19`). |
| **§3.4 (MCP)** | Route `core/mcp_manager.py` through `agent/mcp.py` (`WorkspaceMCP`). | **Rejected** | `agent/mcp.py` is stdio-only; `core/mcp_manager.py:612–667` supports HTTP/SSE transports, custom headers, and auth. Wrapping it over `WorkspaceMCP` would delete remote MCP support. Keep two clients per `I-18` until HTTP/SSE parity is explicitly scoped. |
| **§2.2.7 (Voice)** | Delegate multi-step voice requests via `run_subagent`. | **Deferred** | `run_subagent` requires `context.extra["subagent_runner"]`, injected only by `AgentLoop._run_one` (`agent/loop.py:491–502`). Voice calls `agent_runtime.execute` directly and deliberately omits `run_subagent` (`main.py:2211–2231`). Requires a dedicated voice-completer / mini-loop decision first. |
| **§5.1 / §5.2** | AST gates (`subprocess`, Qt mutators), ratchets, coverage, `mypy --strict`. | **Keep 3 gates; defer rest** | Keep line-count ratchets, tool-registry coverage gate, and `agent/` import purity canary (`I-15`). Defer global `subprocess` AST gate (43 existing files fail today), drop undecidable Qt mutator static gate, use a string test for Forge prompt TLS flags, and move `mypy --strict` to `Q4` backlog. |

---

## 0.1 Landed since Rev 2 (2026-11-01)

Rev 2 was verified against `531b961`. The tree has moved; this table is the delta, so the analysis below is read with the right tense. Historical claims are left as written.

| Batch | Commit | What actually changed | Sections of this document now historical |
|---|---|---|---|
| **B1 (`Q0-A`)** | `20e4803` | Forge writes a staged draft and never executes it; both forge prompts require verified TLS; `SkillCrucible.resolve_dependencies()` returns a plan instead of calling `pip`; the dead `confirmed` declaration is gone; `agent_task` is unregistered. | §1 facts 1–2 (the `execute_sync`/`agent_task` path), §2.1 O-3/O-7, §3.2 layer 1 is still open, §5 B1 row. |
| **B4 (`Q1-2`)** | `31b67d4` | `agent/planner.py`, `executor.py`, `task_queue.py`, `error_handler.py` and `tests/test_autonomous_evolution.py` deleted; `tests/test_architecture_invariants.py` + `tests/architecture_ratchet.json` added. | §1 fact 1 (second half), §3.1 current-state line counts, §5 B4 row is now done. |
| **`Q0-B` mechanical** | `4089451` | Gateway defaults to `127.0.0.1`, `enabled=False`, `advertise=False`; `updater.py`, `core/updater.py`, `core/updater_ota.py` deleted with their startup call and prompt UI. Route auth, DPAPI, and the plugin allowlist are **not** done. | §1 fact 2 (updaters, LAN bind), §2.2.6 is satisfied for git clones only. |
| **Build plumbing** | `112c80f` | `requirements-build.txt` pins PyInstaller + hooks; `scripts/setup_dev.ps1` builds a 3.11 `.venv` and refuses other interpreters; `build_all.ps1` uses `python -m PyInstaller` and writes `dist\SHA256SUMS.txt`; `.github/workflows/build.yml` is manual/tag-only. | §4 gate list (no build gate yet at Rev 2). |
| **ADR-021 / O-8 (`1008`)** | `voice:` batch | `ToolIndex.resolve(pinned_extra=…)` consumes budget, ranks after global pins, evicts oldest-first, and reports `pinned_extra_dropped`; `_handle_search`/`_handle_list_tools` emit literal `call_tool(...)` templates and feed a ring buffer that a `1008` reconnect re-advertises; the unbudgeted Synapse/cdp append is deleted. | §2.1 O-8, §3.2 layer 1 (partially — the `elif` chain is still open), §5 B2 row stays partial. |
| **ADR-019 wave 1 (B6)** | `a3bca15` | Nine native actions (`pdf_document`, `word_document`, `presentation_builder`, `spreadsheet_builder`, `reminder`, `auto_heal`, `self_patch`, `rollback`, `shutdown_brahma`) are `ToolSpec`s dispatched through `AgentRuntime`, with a narrow `HostServices` bundle injected as `ToolContext.extra["host"]`; HUD feedback moved to `tool_start`/`tool_end` events and `tool_end` now carries `output`. `core/tool_exec.py` 865→716 lines and 80→70 `elif`s. | §1 fact 1 (native dispatch — nine tools now gated), §5 B6 row is now done. |
| **ADR-019 wave 2 (B7 partial)** | `bb5c0f0` | Eighteen native specs (`file_controller`, `smart_organizer`, `desktop_organizer`, `save_memory`, `unlock_device`, `connect_*`, `send_message`, Instagram) now route through `AgentRuntime`. `ToolSpec` gained fail-safe per-call `classifier` (`class_for`) and `silent=True`; `core/desktop_host.py` owns `connect_tool_voice`; `ig_gemini_reply` moved to `core/reply_flow.py`. `core/tool_exec.py` 716→527 lines, 70→54 `elif`s; `main.py` 4,811→4,784 lines. | §1 fact 1 (27 native specs now gated), §5 B7 row is now partial (wave 3 + controller extraction remain). |
| **ADR-019 wave 3 (B7 complete)** | `953952d` | All remaining 35 native declarations in `main.py:TOOL_DECLARATIONS` (plus 21 aliases and the retired `agent_task` pointer = 84 desktop specs total across Waves 1–3) now route through `AgentRuntime.execute`. Retired the entire `if/elif` chain in `core/tool_exec.py` (`527→147` lines, `54→0` `elif`s). Extracted `core/text_turn_controller.py` and the remaining session/voice control methods into `core/session_lifecycle.py`, shrinking `main.py` from `4,784` to **`3,233` lines**. | §1 facts 1 & 3 (native `elif` chain retired, `main.py` below 3,903 guard), §3.1, §3.3, §5 B7 row is now done. |
| **ADR-020 / B8 (`Q1-6`)** | `agent:` batch (this one) | `paths.trusted_roots` added to `agent/permissions.py` (containment-only, symlink-resolved, `paths.protected` still enforced, `headless.outside_workspace` untouched, unresolvable roots ignored with diagnostics); `trusted_workspaces.json` store added in `core/user_paths.py`; repo-local `.mcp.json` / `.brahma/mcp.json` gated on workspace trust in `agent/mcp.py`, `agent/__main__.py`, and `core/text_turn_controller.py` with immediate next-turn load/revoke; untrusted workspace instruction files wrapped in `<untrusted-instructions>` delimiters in `agent/prompts.py`. | §2.2.4, §5 B8 row is now done. |

**Numbers that moved:** `main.py` is **3,233** lines with **59** native `TOOL_DECLARATIONS` (plus `load_project_skill` = 60); `ui.py` is **820**; the live ratchet ceilings are `tests/architecture_ratchet.json` (`main.py` 3,233, `ui.py` 827, `core/tool_exec.py` 147, `elif` 0), not the numbers quoted in §4. Test baseline is **812 passed / 1 skipped** (`-k agent`: 407 passed, 1 skipped).

**Still genuinely open** after these batches: gateway HTTP route authentication, DPAPI for `api_keys.json`/`smart_home.key`, the plugin allowlist, `sandbox doctor` (B9), `run_subagent_batch` (B10), and signed releases.

---

## 1. Executive Assessment: Verified Baseline Facts

[`ARCHITECTURE-SPEC.md`](file:///c:/Users/Admin/Brahma-Ai-Evo/docs/ARCHITECTURE-SPEC.md) captures the central dynamic of the repository: **Brahma Evo has a well-engineered, stdlib-only core (`agent/`, 387 passing tests) surrounded by pre-harness application plumbing (`main.py`, `core/tool_exec.py`, `features/`, `agent/executor.py`).**

Five verified structural realities govern the next engineering batches:

1. **Split Execution & Policy Boundaries:** Harness tools pass through [`AgentRuntime.execute`](file:///c:/Users/Admin/Brahma-Ai-Evo/agent/runtime.py), whereas ~60 native actions execute via a ~590-line `elif` chain in [`core/tool_exec.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py#L203-L791) (868 lines total) with no policy engine, and `agent_task` (`core/tool_exec.py:558`) routes to a pre-fork autonomous loop ([`agent/executor.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/agent/executor.py)) that has only an ad-hoc `core.confirm.request` call-screening check (`agent/executor.py:408`) and no `AgentRuntime` policy, containment, or session integration.
2. **Uncontained RCE Surfaces in Default Workflows:** The Skills Forge ([`core/skill_forge.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/core/skill_forge.py)), Crucible ([`core/skill_crucible.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/core/skill_crucible.py)), auto-imported plugins ([`plugin_manager.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/plugin_manager.py)), unauthenticated LAN gateway HTTP routes ([`brahma_connect/gateway/server.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/brahma_connect/gateway/server.py)), a dead `confirmed` declaration (`main.py:1237`; the handler no longer reads it), and unsigned git-reset updaters ([`updater.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/updater.py)) remain open as documented in [`SECURITY-ANALYSIS-ISSUE-4.md`](file:///c:/Users/Admin/Brahma-Ai-Evo/docs/SECURITY-ANALYSIS-ISSUE-4.md).
3. **Host Shell Complexity Breach:** [`main.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/main.py) (4,833 lines, including 60 `TOOL_DECLARATIONS` dicts across lines 808–2188, ~1,380 lines) and [`ui.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/ui.py) (827 lines) exceed the earlier 3,903 / 692 guards.
4. **Protocol Fragility & Budget Breach on Deferred Voice Tools (`1008`):** Gemini Live terminates the WebSocket with `1008` when the model directly calls a tool returned by `search_tools` (`core/tool_bridge.py:609–621`) that was not in the setup frame. Furthermore, an ad-hoc `mcp_Synapse`/`cdp` promotion block in [`core/tool_bridge.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/core/tool_bridge.py#L386-L398) currently appends matching tools *outside* `budget()`, clamped only at `GEMINI_MAX_TOOLS` (128)—an active violation of invariant `I-19`.
5. **Two Intentional MCP Layers (`I-18`):** [`agent/mcp.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/agent/mcp.py) is a stdlib-only stdio client with turn-boundary refresh (`D41`), while [`core/mcp_manager.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/core/mcp_manager.py#L612-L667) is an async SDK client supporting both stdio and remote HTTP/SSE transports with custom headers/auth for the voice path.

> [!IMPORTANT]
> **Core Engineering Thesis:** Every capability in Brahma Evo—across Voice, GUI Text, CLI, MCP, and the future Agent Bridge—must converge onto a canonical [`ToolSpec`](file:///c:/Users/Admin/Brahma-Ai-Evo/agent/spec.py), indexed within the strict session budget by [`core/tool_index.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/core/tool_index.py), and gated by [`AgentRuntime.execute`](file:///c:/Users/Admin/Brahma-Ai-Evo/agent/runtime.py), closing the three ungated execution paths (native `elif`, `DynamicToolRegistry.execute_sync` in Forge, and `agent_task`).

---

## 2. Ratified & Amended Decisions (`O-1` – `O-8` and `§13.1` – `§13.7`)

### 2.1 Owner Decisions (`O-1` through `O-8`)

| ID | Topic | Ratified / Amended Decision | Engineering Contract |
|---|---|---|---|
| **O-1** | Confinement for `run_command` (`N4`) | **Container-First Confinement with Active Behavioral Probe (`sandbox doctor` + `require_confined`).** | WSL2 alone is **not** confinement (it mounts host drives and shares networking by default). `python -m agent sandbox doctor` must actively probe the configured `exec.sandbox.prefix` by attempting: (1) a write outside the workspace mount, (2) a loopback/external network connection, and (3) a UID/privilege check. `exec.sandbox.require_confined` stays off unless the probe passes (the doctor reports only; it never writes policy). |
| **O-2** | Subagent Fan-Out (`N5`) | **Keep Mutation Sequential; Allow Read-Only `run_subagent_batch` Under a Strict Safety Contract.** | A read-only fan-out batch is permitted only if it enforces one runtime boundary (`I-08`): (1) child surface restricted to `tool_class == "read"` (no `mcp`, `native`, `write`, `exec`, `network`, or `device`), (2) path arguments restricted strictly to inside the workspace (`paths.outside_workspace` and `paths.protected` denied without prompt), (3) any tool with `requires_approval=True` excluded, (4) `run_subagent` and `run_subagent_batch` excluded from the **child's** surface, and (5) all children share the parent's `AgentRuntime` and `cancel_event`. |
| **O-3** | Security Phase 0 Scope | **Split into Two Atomic Batches: `Q0-A` (Execution/Code) and `Q0-B` (Network/Storage/Supply Chain).** | `Q0-A` locks down Forge (`execute_sync` removal), Forge prompt TLS bypasses, Crucible runtime `pip`, the dead `confirmed` declaration, and `agent_task`. `Q0-B` locks down Gateway loopback + HTTP route auth, DPAPI secrets, plugin allowlists, and unsigned `git reset --hard` updaters. |
| **O-4** | Agent & Orchestrator Bridge | **Re-base onto `AgentRuntime` via `ADR-018` ("Bridge as a Tool Source, Never a Runtime").** | Every bridged agent verb registers as a `ToolSpec` gated by `AgentRuntime.execute`. Keep read-only opt-in discovery, `AgentRecord` (storing secret references, never raw tokens), durable artifacts (`output_ref`), and `dry_run` budgets. |
| **O-5** | Upstream Integration Timing | **Port Selectively After `Q0-A` and `Q0-B` Land.** | Freeze upstream porting until both Phase 0 security batches are on `main`. Never merge `9dd02cf` wholesale. |
| **O-6** | Native-Tool Policy & Dispatch | **Incremental Migration of `actions/*` into `ToolSpec` Using Existing `TOOL_CLASSES`.** | Migrate native actions by risk class (`exec` → `write` → `network`/`device` → `read`) using the existing `TOOL_CLASSES` tuple in [`agent/spec.py`](file:///c:/Users/Admin/Brahma-Ai-Evo/agent/spec.py#L30) (`"read"`, `"write"`, `"exec"`, `"network"`, `"device"`, `"mcp"`, `"native"`). Do not add `is_async` or `ui_feedback` to `ToolSpec` (`core/tool_exec.py:131` already dispatches on a worker thread, and `actions/` is synchronous). |
| **O-7** | Legacy `agent_task` Executor | **Disable Immediately in `Q0-A`; Delete Legacy Files in `Q1`.** | Unregister `agent_task` in `Q0-A`, then delete `agent/planner.py`, `agent/executor.py`, `agent/task_queue.py`, and `agent/error_handler.py` in `Q1` so `agent/` contains only the stdlib harness. |
| **O-8** | Deferred-Tool `1008` Voice Crash | **Call-Template Search Formatting + Session-Scoped `pinned_extra` on Reconnect + Delete Unbudgeted Promotion.** | (1) Format `_handle_search` and `_handle_list_tools` results as literal `call_tool(tool_name="...", arguments={...})` templates; (2) add session-scoped `pinned_extra` to `ToolIndex.resolve()` and `build_session_payload()` for `1008` reconnects without replaying the user turn; (3) delete the unbudgeted `mcp_Synapse` promotion loop in `core/tool_bridge.py:386–398` to restore `I-19`. |

---

### 2.2 Dispositions on Open Architectural Questions (`§13.1` – `§13.7`)

1. **Native Action Catalogue vs. Harness (`§13.1`):**
   - **Disposition:** **Unified `ToolSpec` Registry.** The end state is that every native capability in `actions/*.py` is a registered `ToolSpec` classified under `agent/spec.py:TOOL_CLASSES` (`read`, `write`, `exec`, `network`, `device`, `native`) and gated by `AgentRuntime.execute`. `agent/` remains stdlib-only (`I-15`); desktop action `ToolSpec`s are registered by the host layer at startup.
2. **Gateway Positioning (`§13.2`):**
   - **Disposition:** **Opt-In Companion Feature with Loopback Default and Full Route Auth.** Bind `127.0.0.1` and default `off`. Every HTTP control route (`/gateway/info`, `/pair`, `/devices`, `/logs`, `/pending`, approve/reject) must enforce paired-device authentication on par with the WebSocket channel.
3. **Orchestrator Topology (`§13.3`):**
   - **Disposition:** **Strict 1-Hop Hierarchy (Supervisor → Leaf Worker).** Brahma supervises subordinate adapters (`depth=0 → depth=1`) with self-target refusal and hop-count headers; peer-fleet mesh topologies remain deferred (`ADR-013`).
4. **Trusted-Input Policy for Cloned Repositories (`§13.4`):**
   - **Disposition:** **Workspace Trust Fingerprinting.** Record user-approved workspace roots in a local `trusted_workspaces` table before auto-loading repo-local `.mcp.json` servers or project instructions from an unfamiliar directory, and wrap external file/search outputs in explicit untrusted delimiters when taint policy requires it.
5. **Skills Forge Survival (`§13.5`):**
   - **Disposition:** **Quarantined Draft Studio Only.** Forged skills are written to a staging directory, checked with static AST rules and a subprocess timeout, and presented as a diff in `core/confirm.py`. Only user-approved skills with a recorded SHA-256 entry are loaded by `DynamicToolRegistry`.
6. **Release & Update Model (`§13.6`):**
   - **Disposition:** **No Self-Resetting Git Clones; Signed Release Artifacts for Packaged Builds.** Remove `git reset --hard` and the hardcoded remote from `updater.py` and `core/updater.py`. Git clones are notify-only; end-user binary releases must verify signatures/checksums before applying updates.
7. **Voice Loop vs. Harness Loop (`§13.7`):**
   - **Disposition:** **Defer Loop Unification; Keep Separate Until a Voice-Completer Architecture is Designed.** Gemini Live owns a stateful bidirectional audio WebSocket (`I-20`), whereas `AgentLoop` drives stateless text completion turns and injects `subagent_runner` (`agent/loop.py:491–502`). Advertising `run_subagent` to voice without a text completer fails immediately (`main.py:2211–2231`). Keep the loops separate for now; revisit a dedicated voice-to-`AgentLoop` bridge only after `Q0` and `Q1` are complete.

---

## 3. Corrected Subsystem Designs

### 3.1 Native Action Migration & Retiring the `core/tool_exec.py` `elif` Chain (`O-6`)

- **Current State:** `main.py:808–2188` holds 60 `TOOL_DECLARATIONS` dicts (~1,380 lines), and `core/tool_exec.py:203–791` (~590 lines of an 868-line file) dispatches them via an `if/elif` cascade outside `AgentRuntime`.
- **Target State:**
  1. Register each native action as a `ToolSpec` using the existing `TOOL_CLASSES` (`"read"`, `"write"`, `"exec"`, `"network"`, `"device"`, `"mcp"`, `"native"`).
  2. Migrate in four incremental batches ordered by risk class:
     - **Wave 1 (`exec` & `write`):** process launchers, file mutators, clipboard/system writers.
     - **Wave 2 (`device` & `network`):** OS automation, window/input control, smart-home, web/API actions.
     - **Wave 3 (`read` & remaining `native`):** system status, screen/window inspection, read-only queries.
  3. Because `core/tool_exec.py:131` already runs tool execution off the GUI thread and `actions/` contains only synchronous functions, `ToolSpec` requires **no** `is_async` or `ui_feedback` extensions.
  4. Once Wave 3 completes, delete the `elif` chain in `core/tool_exec.py`, reducing `core/tool_exec.py` from 868 lines to ≤ 150 lines and making `I-01` (`AgentRuntime.execute` as the single boundary) universal across all tools.

---

### 3.2 Solving the Gemini Live `1008` Deferred-Tool Crash & Restoring `I-19` (`O-8`)

1. **Layer 1 — Affordance Shaping in `_handle_search` & `_handle_list_tools` (`core/tool_bridge.py:579–660`):**
   Replace declaration-shaped search output (`tool_name: X`, `arguments: a, b`) with copy-pasteable `call_tool` templates so the model never synthesizes an undeclared top-level function call:
   ```text
   1. [DEFERRED — MUST USE call_tool] Group: synapse | Does: Open a browser tab
      Call exactly as: call_tool(tool_name="mcp_Synapse_cdp_open_tab", arguments={"url": "<string>"})
   ```
2. **Layer 2 — Fix the Existing `I-19` Budget Violation (`core/tool_bridge.py:386–398`):**
   Delete the ad-hoc `mcp_Synapse` / `cdp` loop in `build_session_payload()` that appends declarations *after* `index.resolve(intent, budget=budget())`. All tool selection must happen inside `ToolIndex.resolve()` under `budget()` so `I-19` holds unconditionally.
3. **Layer 3 — Session-Scoped `pinned_extra` on `1008` Reconnect (`core/tool_index.py`, `core/tool_bridge.py`, `core/session_lifecycle.py`):**
   - Extend `ToolIndex.resolve(self, intent: str = "", *, budget: int = DEFAULT_BUDGET, pinned_extra: Iterable[str] = ())` and `tool_bridge.build_session_payload(intent: str = "", *, pinned_extra: Iterable[str] = ())`.
   - Tools named in `pinned_extra` are treated as pinned **for that single `resolve()` call only** (never mutating `entry.pinned` on the shared `ToolIndex`), consuming slots from the top-k retrieval budget so total declarations still strictly obey `budget()` (`I-19`).
   - In `core/session_lifecycle.py`, maintain a session-local ring buffer of tool names returned by `search_tools` / `list_tools` during the active Live connection. If the WebSocket drops with `1008`, pass those names as `pinned_extra` into `_build_config()` on reconnect.
   - **Never replay the user turn on reconnect:** Gemini Live session resumption already restores conversation context, and `_last_user_text` (`core/session_lifecycle.py:523–527`) is used strictly as the retrieval query string.

---

### 3.3 `main.py` Decomposition Target (`< 1,500` Lines)

Moving the 60 `TOOL_DECLARATIONS` dicts (~1,380 lines) out of `main.py` into per-domain `ToolSpec` registrations immediately drops `main.py` from 4,833 lines to ~3,450 lines. Extracting `core/text_turn_controller.py`, `core/voice_turn_controller.py`, `core/forge_controller.py`, and `core/app_bootstrap.py` brings `main.py` under **1,500 lines** (well below the historical 3,903 guard) without altering runtime behavior.

---

## 4. Pragmatic CI Gates (`tests/test_architecture_invariants.py`)

Only deterministic, immediately green checks belong in CI gates:

1. **File Size Ratchets (`C-4`):** Assert line counts of `main.py` ($\le 4,833$, ratcheted down to $< 1,500$ as extractions land), `ui.py` ($\le 827$), and `core/tool_exec.py` ($\le 868$, ratcheted down to $\le 150$).
2. **Import Purity Canary (`I-15`):** Subprocess check verifying that importing `agent.loop`, `agent.runtime`, `agent.mcp`, and `agent.code_tools` leaves `PyQt6`, `google.genai`, and `comtypes` absent from `sys.modules`.
3. **Budget Invariant Gate (`I-19`):** Verify `build_session_payload(intent)` never returns more than `budget()` declarations for any intent (including `"chrome browser synapse cdp open tab"`).
4. **Forge Prompt Hygiene Test:** String assertion test verifying `verify=False` and `_create_unverified_context` never appear in `core/skill_forge.py`.
5. **ToolSpec Coverage Ratchet (`I-01`, `I-28`):** Track the count of remaining `elif name ==` branches in `core/tool_exec.py` so no new `elif` branch can ever be added, ratcheting the allowed count down to `0` across the `O-6` migration waves.

*(Global `subprocess` AST bans across legacy modules and `mypy --strict` across the repository are deferred to the `Q4` assurance queue after legacy cleanup.)*

---

## 5. Corrected Single-Concern Batch Sequence

Every batch below adheres strictly to `ARCHITECTURE-SPEC.md` §10.1 (**one concern per batch, one commit, tests + docs together**) and respects the `Q0 → Q1 → Q2/Q3 → Q4` ordering:

| Batch | Prefix | Single Concern & Deliverables | Gate / Verification |
|---|---|---|---|
| **B1 (`Q0-A`)** ✅ | `security:` | **Execution & Code Lockdown:** Remove `execute_sync` from `_forge_skill` and `agent/executor.py`; remove TLS bypass strings from `core/skill_forge.py` prompts; make `SkillCrucible.resolve_dependencies()` return a plan without calling `pip`; delete the dead `confirmed` declaration at `main.py:1237` (the handler no longer reads it) and update the finding status; disable `agent_task` declaration and handler. | Unit tests for Forge draft-only, prompt string check, a schema test that no declaration advertises `confirmed`, `agent_task` disabled. |
| **B2 (`Q0-B`)** ◐ | `security:` | **Network, Secrets & Supply Chain Lockdown:** Bind Gateway to `127.0.0.1` (default off) and require paired-device auth on all HTTP control routes; move `api_keys.json` and `smart_home.key` behind Windows DPAPI (`ctypes.windll.crypt32`) with 1-release read-only fallback; add opt-in allowlist for `plugin_manager.py`; remove `git reset --hard` and hardcoded remote from `updater.py` / `core/updater.py`. **Landed:** loopback/off defaults + no-advertise, and the updater modules are deleted outright. **Open:** route auth, DPAPI, plugin allowlist. | Gateway default tests, opt-in override, disabled-never-starts, no-updater guards. |
| **B3 (`Q1-1`)** | `docs:` | **Record Decisions & `ADR-018`:** Record ratified `O-1`…`O-8` in `AGENTIC-HARNESS-STATUS.md` / `AGENTIC-HARNESS-NEXT.md` and write `ADR-018` (Agent Bridge as a `ToolSpec` source behind `AgentRuntime`, superseding `ADR-006`/`ADR-011`). | Docs in sync across Tier 0/1 files. |
| **B4 (`Q1-2`)** ✅ | `agent:` | **Retire Legacy Autonomous Executor & Add Ratchet Gates:** Delete `agent/planner.py`, `agent/executor.py`, `agent/task_queue.py`, and `agent/error_handler.py`; add `tests/test_architecture_invariants.py` (line-count ratchets, `I-15` canary, `elif` count ratchet). | `pytest tests -q -k agent` green; `agent/` contains only harness modules. |
| **B5 (`Q1-3`)** ✅ | `voice:` | **Resolve Deferred-Tool `1008` & Restore `I-19` (`O-8`):** Format `search_tools`/`list_tools` hits as `call_tool(...)` templates; delete the unbudgeted `mcp_Synapse` promotion block in `core/tool_bridge.py:386–398`; add session-scoped `pinned_extra` to `ToolIndex.resolve()` and `build_session_payload()` for `1008` reconnects. **Landed (`65fca34`, ADR-021).** | `test_tool_index.py` and `test_tool_bridge.py` verify strict `budget()` cap and `pinned_extra` scoping. |
| **B6 (`Q1-4`)** ✅ | `agent:` | **Native Action Migration Wave 1 (`exec` & `write` tools):** Convert mutating and process-spawning native declarations into `ToolSpec`s routed through `AgentRuntime.execute`; lower the `core/tool_exec.py` `elif` ratchet. **Landed (ADR-019 wave 1):** 9 tools, `core/desktop_host.py` + `core/desktop_tools.py`, 865→716 lines, 80→70 `elif`s. | `tests/test_desktop_tools.py` (policy denial, headless exec denial, `requires_approval`, recording host, HUD events). |
| **B7 (`Q1-5`)** ✅ | `agent:` | **Native Action Migration Waves 2–3 & `main.py` Controller Extraction:** Convert remaining `device`, `network`, and `read` native tools to `ToolSpec`s; delete the `elif` chain in `core/tool_exec.py`; extract turn controllers so `main.py` shrinks below its historical guard. **Landed (ADR-019 waves 2–3):** 84 desktop specs total across Waves 1–3 (`core/desktop_tools.py`), `core/tool_exec.py` 716→147 lines and 70→0 `elif`s; `core/text_turn_controller.py` and `core/session_lifecycle.py` extracted, shrinking `main.py` 4,811→3,233 lines. | `elif` count == 0; `I-01` universal; `main.py` ≤ 3,233 lines. |
| **B8 (`Q1-6`)** ✅ | `agent:` | **Workspace Trust Fingerprinting (`§13.4`):** Add `trusted_workspaces` store and gate repo-local `.mcp.json` / `.brahma` auto-loading on workspace trust approval; add `paths.trusted_roots` and untrusted instruction delimiters (`ADR-020`). | Workspace trust unit tests in `tests/test_agent_permissions.py`, `test_agent_prompts.py`, and `test_agent_mcp.py`. |
| **B9 (`Q1-7`)** | `agent:` | **Verified Container Confinement (`N4` / `O-1`):** Add `python -m agent sandbox doctor` behavioral probe (write-outside, egress, UID) and `exec.sandbox.require_confined` policy enforcement. | `test_agent_sandbox.py` probe verification tests. |
| **B10 (`Q1-8`)** | `agent:` | **Read-Only Subagent Fan-Out (`N5` / `O-2`):** Add `run_subagent_batch` enforcing the strict read-only, workspace-confined, no-approval, single-runtime contract, with both delegation tools excluded from the child surface. | `test_agent_subagent.py` fan-out & boundary enforcement tests. |
| **B11 (build)** ✅ | `build:` | **Rebuildable Clean Clone:** Pin PyInstaller + hooks in `requirements-build.txt`, add `scripts/setup_dev.ps1` (3.11 only, creates `.venv`, installs runtime + dev + build sets), make `build_all.ps1` use `python -m PyInstaller` and emit `dist\SHA256SUMS.txt`, add a manual/tag-only `build` workflow. | `tests/test_build_reproducibility.py` static guards; both scripts' failure paths exercised. |
