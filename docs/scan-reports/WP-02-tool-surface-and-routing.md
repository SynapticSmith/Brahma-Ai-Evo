# WP-02: Tool Surface, Retrieval Index, Routing Authority & Unified Dispatch — Deep Scan & Analysis Report

- **Work Package:** WP-02
- **Baseline Commit:** `6439b2e` (`Make the subagent library visible on voice and first-call surfaces`)
- **Files Scanned:** 12 files (8,759 lines of Python) + related test suites and design specifications
- **Overall Subsystem Health:** **Needs Hardening** (two critical execution & concurrency vulnerabilities found: `DynamicToolRegistry` bypass of `AgentRuntime.execute`, and cross-thread failure in `ToolIndex` FTS5 SQLite index; plus uncontained `exec()` in `desktop_control` under default-allowed `device` class)

---

## 1. Subsystem Architecture & Data-Flow Summary

The tooling, retrieval, and routing architecture in Brahma AI Evo forms the central nervous system connecting user utterances (from voice, GUI text, or CLI) to execution capabilities (harness code primitives, 84 desktop action adapters, MCP tools, and dynamic features).

Following architectural decisions ADR-019 (Action Host Injection), ADR-021 (Pinned Extra & Retrieval Semantics), and the Plane-A/Plane-B routing overhaul (documented in `docs/ROUTING-AUDIT.md`, `docs/TOOL-RETRIEVAL-DESIGN.md`, and `docs/ROUTING-IMPLEMENTATION.md`), the subsystem is partitioned into two distinct planes:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ PLANE A: Pre-LLM Utterance Routing (core/routing.py, core/text_turn_controller.py)     │
│ User Text → 23-Record ROUTERS Inventory                                                │
│   ├── Syntax (Slash commands, /sessions, exact command grammar)                        │
│   ├── Exact Alias (Normalised equality: dynamic skill trigger, open_app alias)         │
│   ├── Owned State (Offline/online, modal reply, active phone target)                   │
│   └── Verified Target (Brahma Connect phone word + active paired device)               │
│                                                                                        │
│ If NO deterministic authority proves a claim → DECLINE turn to Plane B                 │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ PLANE B: Model & Tool-Surface Routing (core/tool_index.py, core/tool_bridge.py)        │
│ Fixed Gemini Live Surface: <=24 declarations (Pinned + Meta-tools + Top-k Retrieved)   │
│   ├── Pinned Tools: undo, recall_memory, read_file, edit_file, run_command             │
│   ├── Meta-Tools: search_tools, call_tool, list_tools, get_skill_manual, etc.         │
│   ├── Hybrid Retrieval: FTS5 (BM25) + N-gram (TF-IDF) + Embed (all-MiniLM-L6-v2) RRF   │
│   └── Ambiguity Contrast Set: Deterministic margin test across server owners           │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ UNIFIED DISPATCH SEAM (core/tool_exec.py)                                              │
│ ToolExecutor.execute (0 elif branches, 147 lines ratchet ceiling)                      │
│   ├── ToolSpec in agent.spec? ───────► AgentRuntime.execute (Policy -> Approval)      │
│   │                                       ├── Harness code tools (agent/code_tools.py) │
│   │                                       ├── 84 Desktop ToolSpecs (desktop_tools.py)  │
│   │                                       └── Harness MCP tools (agent/mcp.py)         │
│   ├── Core Meta-tool? ────────────────► core/tool_bridge.py:execute_meta               │
│   └── Unregistered Dynamic Tool? ─────► DynamicToolRegistry.execute_sync ⚠️ (BYPASS)  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Key Components & Responsibilities
1. **Pre-LLM Routing Authority (`core/routing.py`):** Establishes the `Authority` enum (`syntax`, `exact_alias`, `owned_state`, `verified_target`) and the 23-record `ROUTERS` registry. It completely replaces fuzzy lexical matching with whole-word bounded helpers (`has_phrase`, `any_phrase`, `is_phrase`, `exact_alias`).
2. **Unified Dispatch Seam (`core/tool_exec.py`):** Replaces the legacy 1,200-line `elif` chain from pre-ADR-019. Locked at $\le 147$ lines with exactly 0 `elif` branches, it delegates tool execution to `AgentRuntime.execute` for all registered specs.
3. **Retrieval Index (`core/tool_index.py`):** Implements in-memory SQLite FTS5 BM25, numpy-based hashed n-gram TF-IDF cosine similarity, and optional `fastembed` ONNX micro-embeddings (`sentence-transformers/all-MiniLM-L6-v2`) fused via Reciprocal Rank Fusion (RRF, $k=60$). Resolves up to 128 declarations within a user-configurable budget (default 24).
4. **Meta-Tool Bridge (`core/tool_bridge.py`):** Hosts the meta-tools that let Gemini Live query and invoke deferred tools (`search_tools`, `call_tool`, `list_tools`, `get_skill_manual`, `get_tool_surface`, `search_skills`, `list_skills`, `load_skill`). Manages the 2-turn memory-only turn context ring buffer for query fusion.
5. **Desktop Tool Adapters (`core/desktop_tools.py`):** Exposes all 59 native capability declarations from `main.py:TOOL_DECLARATIONS` and 38 aliases (total 97 specs across `WAVE1`, `WAVE2`, and `WAVE3`) as canonical `ToolSpec` objects tagged `"desktop"`, providing per-call classification for read/write verbs.
6. **Host Services Injection (`core/desktop_host.py`):** Bundles host UI and TTS operations into `HostServices` (`ui`, `speak`, `submit`), decoupling desktop actions from PyQt6 globals and preventing spoken TTS banners from corrupting the Gemini Live audio pipeline.
7. **Dynamic Tool Registry (`core/dynamic_registry.py`):** Hot-loads model-forged skills and codebase features from `features/` and AppData skills vault.
8. **Undo Stack & Diff Preview (`core/undo.py`, `core/diff_preview.py`):** Provides a thread-safe 10-deep reverse callable stack for reversible file and desktop operations, and unified diff hunk folding for interactive approval banners.
9. **Dormant Engines (`core/intent_dispatcher.py`, `core/execution_reflection.py`):** Legacy 3-tier reflex/cache/LLM intent router and post-execution reflection engine, pinned as dormant to prevent accidental re-wiring.

---

## 2. File-by-File Inventory & Health Matrix

| File Path | Lines | Primary Symbols / Classes | Status | Risk Level | Notes |
|---|---:|---|---|---|---|
| `core/tool_exec.py` | 147 | `ToolExecutor`, `_LocalFC`, `TOOL_EXEC_TIMEOUT_SECONDS` | Active | **High** | Ratchet-locked at 147 lines, 0 `elif` branches. Dispatches specs to `AgentRuntime.execute`. **Critical Fallback Bypass:** Unregistered tools fall through directly to `DynamicToolRegistry.execute_sync` outside `AgentRuntime`. |
| `core/tool_index.py` | 2,168 | `ToolIndex`, `ToolEntry`, `SearchHit`, `SkillManual`, `_Fts5Backend`, `_NgramBackend`, `_EmbedBackend`, `_EmbedCache`, `META_DECLARATIONS` | Active | **High** | Hybrid RRF index. **Critical Threading Bug:** SQLite `:memory:` connection in `_Fts5Backend` is created without `check_same_thread=False`, causing cross-thread queries from worker threads to fail silently. |
| `core/tool_bridge.py` | 1,605 | `configure`, `build_session_payload`, `execute_meta`, `_handle_search`, `_handle_call`, `set_turn_context`, `turn_context` | Active | Medium | Manages meta-tools, session payload, and turn context ring buffer. `call_tool` enforces collision guards and bare-name disambiguation, but `_executor` path lacks synchronization on loop prevention structures. |
| `core/tool_policy.py` | 213 | `is_shell_command_safe`, `get_required_capabilities`, `evaluate_tool_call`, `execute_hardened_bash`, `execute_hardened_python_repl` | Dormant / Test-Only | Medium | Capability-based security policy and hardened runner engine. Fully implemented but completely un-wired from production dispatch; only called by test suites. |
| `core/desktop_tools.py` | 1,688 | `WAVE1`, `WAVE2`, `WAVE3`, `ALIASES`, `CLASSIFIERS`, `HANDLERS`, `build_specs`, `register`, `attach_host` | Active | **High** | 97 `ToolSpec` definitions covering all 59 native declarations. **Security Risk:** `desktop_control` is classified as `device` (default allow), yet executes dynamic LLM-generated code via `exec()` with `ctypes`. |
| `core/desktop_host.py` | 469 | `HostServices`, `HostUI`, `HostEventSink`, `LiveHost`, `RecordingHost`, `present_start`, `present_end` | Active | Low | Clean ADR-019 implementation. Encapsulates HUD deliverable presentation and speech feedback without module globals. |
| `core/dynamic_registry.py` | 634 | `DynamicToolRegistry`, `DynamicSkill`, `_ExternalSkillModule`, `find_matching_skill`, `execute_sync` | Active | **High** | Hot-loads features from `features/`. Executes codebase features directly in-process without `SkillHost` AppContainer isolation or `AgentRuntime` policy checks. |
| `core/routing.py` | 369 | `Authority`, `RoutingClaim`, `RouterRecord`, `ROUTERS`, `tokens`, `normalize`, `has_phrase`, `any_phrase`, `exact_alias` | Active | Low | Authoritative Plane-A inventory (23 records). All phrase matching is word-bounded. Enforced by 105 tests in `test_routing_authority.py`. |
| `core/intent_dispatcher.py` | 1,051 | `IntentDispatcher`, `IntentResult` | Dormant | Low | 1,051-line legacy 3-tier pre-LLM dispatcher. Not imported by live application code; pinned dormant by ratchet test. Candidate for eventual deletion. |
| `core/execution_reflection.py` | 154 | `ExecutionReflectionEngine`, `ReflectionResult` | Dormant | Low | Post-execution verification engine. Not imported by live application code; pinned dormant by ratchet test. |
| `core/undo.py` | 121 | `push_undo`, `undo_last`, `peek`, `history`, `clear`, `MAX_DEPTH` | Active | Low | Shared undo stack bounded to 10 entries with thread lock. Reversible operations register simple lambdas. |
| `core/diff_preview.py` | 140 | `DiffPreview`, `preview_detail`, `fold_diff`, `count_changes`, `split_detail` | Active | Low | Pure Python diff parser and hunk folder for confirmation dialogs. Tested and free of UI dependencies. |

---

## 3. Invariant & Specific Audit Question Verdicts

### 3.1 Architectural Invariant Health Matrix

| Invariant ID | Verdict | Evidence (`file:line` & symbol) | Explanation |
|---|---|---|---|
| **I-01** | **PARTIAL / GAP** | `core/tool_exec.py:126-130` (`ToolExecutor.execute`), `core/text_turn_controller.py:359` | All 84 desktop tools and harness tools pass through `AgentRuntime.execute`. However, when an unrecognized tool name matches `DynamicToolRegistry.has_tool(name)`, or when `find_matching_skill` matches a feature, execution falls through directly to `DynamicToolRegistry.execute_sync`, completely bypassing `AgentRuntime.execute`, policy evaluation, and interactive confirmations. |
| **I-02** | **PASS (SUPERSEDED)** | `core/desktop_tools.py:1-1688` (`register`), `core/tool_exec.py:88-100` | Native tools were historically un-gated by policy (scoped invariant I-02). Under ADR-019 Waves 1–3, all 84 native capabilities are now converted to `ToolSpec` adapters registered into `agent.spec` and dispatched through `AgentRuntime.execute`. |
| **I-07** | **PASS** | `agent/spec.py`, `agent/skills.py`, `core/tool_bridge.py:649` | No harness tool in `agent/` shadows core meta-tools (`search_tools`, `call_tool`, `list_tools`, `load_skill`, `recall_memory`). Harness skill tool is explicitly named `load_project_skill` to prevent collisions. |
| **I-19** | **PASS** | `core/tool_bridge.py:185-199` (`budget`), `core/tool_index.py:1694-1738` (`resolve`) | Advertised tool surface sent to Gemini Live is strictly bounded: `budget()` clamps to $[6, 128]$ (default 24). Declarations are composed of fixed pins (5) + meta-tools (8) + retrieved top-$k$, guaranteed never to overflow the 128 protocol ceiling. |
| **I-20** | **PASS** | `core/tool_bridge.py:17-31`, `core/session_lifecycle.py:484-513` | No code assumes mid-session tool swapping on Gemini Live. Unadvertised capabilities are accessed exclusively via `search_tools` and `call_tool`. |
| **I-28** | **PASS** | `core/tool_exec.py:1-147` (`ToolExecutor`) | `core/tool_exec.py` contains exactly **0 `elif` branches** and is locked at 147 lines (checked by architecture ratchet in `tests/test_tool_exec_ratchet.py`). Native tools are registered as `ToolSpec`s in `core/desktop_tools.py`. |

---

### 3.2 Specific Audit Question Verdicts

#### Question 1: Completeness of `core/desktop_tools.py` Mapping
**Verdict:** **COMPLETE MAPPING (59/59 declarations, 0 parameter mismatches), BUT 2 CRITICAL CLASSIFICATION VULNERABILITIES IDENTIFIED.**

- **Inventory Reconciliation:**
  - `main.py:TOOL_DECLARATIONS` contains 66 total declarations at runtime. Of these, 7 are harness code primitives dynamically appended at line 2151 (`read_file`, `write_file`, `edit_file`, `glob_files`, `grep_files`, `run_command`, `list_subagents`).
  - The remaining **59 declarations** are the static native capability declarations.
  - Automated cross-check of all 59 declarations against `WAVE1` (9 entries), `WAVE2` (18 entries), and `WAVE3` (70 entries) in `core/desktop_tools.py` confirmed **100% presence** (0 missing native tools).
  - Cross-check of all parameter names between `main.py:TOOL_DECLARATIONS` and `desktop_tools.build_specs()` revealed **0 parameter mismatches**.
  - All 29 aliases in `ALIASES` correctly map legacy names (e.g. `self_patch` $\rightarrow$ `auto_heal`, `rollback` $\rightarrow$ `auto_heal`, `diagnostics` $\rightarrow$ `system_diagnostics`, `create_image` $\rightarrow$ `generate_image`).
  - Fallback declarations in `_get_fallback_declaration` properly cover the 8 plugins not in `TOOL_DECLARATIONS` (`image_generator`, `reverse_engineer`, `game_sentinel`, `study_guard`, `net_sentinel`, `flowstate`, `shopping_agent`, `space_cleaner`).
- **Tool Class Permissiveness Analysis:**
  - `desktop_control`: Declared as `("device", False)` in `WAVE3:101`. In `actions/desktop.py:397-414`, when `action == "task"`, it passes the user's task prompt to Gemini, requests raw Python code, and executes it via `_execute_generated_code()` using `exec(compile(code, "<brahma_desktop>", "exec"), sandbox)`. Because `sandbox` includes `ctypes`, `pyautogui`, `shutil`, and `Path`, and the policy class `device` defaults to `ALLOW`, an LLM invoking `desktop_control(task="...")` executes arbitrary code in the main process with zero human confirmation! **This is a critical misclassification.**
  - `computer_control`: Declared as `("device", False)` in `WAVE3:102`. Directly invokes `pyautogui` mouse clicks, drags, hotkeys, and keyboard keystrokes (`actions/computer_control.py:384-447`). Default `ALLOW` on `device` allows unprompted OS interaction.
  - `skill_forge`: Declared as `("write", False)` in `WAVE3:129`. While generating code is writing, it writes executable `.py` files directly into `features/` or staged skills.
  - `space_cleaner`: Action `purge` deletes temporary directories and browser caches; classified as `write` (default `ALLOW`) rather than requiring confirmation.

#### Question 2: `DynamicToolRegistry` Fallback in `core/tool_exec.py`
**Verdict:** **CONFIRMED BYPASS OF `AgentRuntime.execute` AND PERMISSION POLICY.**

- **Execution Chain:**
  1. A tool call arrives at `ToolExecutor.execute(fc)` (`core/tool_exec.py:74`).
  2. `spec = get_spec(dispatch_name)` evaluates to `None` for any forged or dynamic skill not registered in `agent.spec`.
  3. `tool_bridge.is_meta_tool(name)` is `False`.
  4. Execution reaches lines 126–130:
     ```python
     if name and DynamicToolRegistry.has_tool(name):
         loop = asyncio.get_event_loop()
         run_res = await loop.run_in_executor(
             None, lambda: DynamicToolRegistry.execute_sync(name, args or {})
         )
     ```
  5. `DynamicToolRegistry.execute_sync` calls `DynamicSkill.execute_sync` (`core/dynamic_registry.py:76-94`).
  6. For all codebase features in `features/*.py`, `self.is_synthetic` is `False`. `self._load_module()` dynamically imports the module via `importlib.util.spec_from_file_location` and calls `module.execute(**kwargs)` directly inside the host process.
- **Security & Policy Implications:**
  - **Zero Policy Checks:** `AgentRuntime.execute` is never invoked. The permission policy file (`agent_permissions.json`), path scoping rules (`paths.protected`, `paths.outside_workspace`), and headless rules are completely bypassed.
  - **Zero Approval Prompts:** Destructive or exec-heavy operations implemented in a dynamic feature run without triggering `core/confirm.py`.
  - **No Containment:** The AppContainer isolation in `core/skill_host.py` is only used if `self.is_synthetic is True` and `self.module is None`. Standard features in `features/*.py` run with full user privileges in the host process.
  - **Dual Seam:** The exact same bypass occurs in `core/text_turn_controller.py:359` when `find_matching_skill` matches a turn.

#### Question 3: `call_tool` Meta-Tool Security & Loop Prevention
**Verdict:** **AMBIGUITY & DISABLED GUARDS ACTIVE, BUT RECURSION GUARD IS INEFFECTIVE AND DYNAMIC SKILLS EXPLOIT THE DISPATCH HOLE.**

- **Call Chain Trace:**
  $$\text{Model} \xrightarrow{\text{call\_tool}} \text{ToolExecutor.execute} \xrightarrow{\text{meta}} \text{tool\_bridge.execute\_meta} \xrightarrow{\text{\_handle\_call}} \text{main.\_execute\_tool\_via\_bridge} \xrightarrow{} \text{ToolExecutor.execute\_via\_bridge} \xrightarrow{} \text{execute\_sync} \xrightarrow{} \text{AgentRuntime.execute}$$
- **Disabled Tools:** `_handle_call` (`core/tool_bridge.py:1336`) calls `entry = index.get(tool_name)`. `ToolIndex.get(name)` explicitly checks `if name in self._disabled: return None`. Disabled tools return `"Error: no tool named '{tool_name}'."` and cannot be invoked via `call_tool`.
- **Recursive `call_tool`:** `META_TOOLS` are not in `ToolIndex._entries`. `index.get("call_tool")` returns `None`. An attempt to call `call_tool(tool_name="call_tool", ...)` is rejected as an unknown tool.
- **Ambiguity Guards:** `_handle_call` checks `collision = index.collision_for(tool_name)`. If multiple servers provide the exact same name, it rejects execution (`line 1329`). If an unqualified bare name is passed, `_bare_name_candidates()` detects multi-server collisions and returns an error with the qualified names (`line 1346`).
- **Loop Prevention Defect:** In `ToolExecutor.execute_via_bridge` (`core/tool_exec.py:44-54`), `self._bridge_depth` is incremented and decremented, **but never evaluated against a threshold**. Loop detection relies solely on `self._recent_calls.count(key) > 5`. An alternating cycle of calls `(Tool_A, Tool_B, Tool_A, Tool_B...)` is not broken by `_bridge_depth`, and `self._recent_calls` is mutated across threads without a lock.
- **Dynamic Tool Dispatch Exploit:** If a dynamic skill from `features/` is indexed in `ToolIndex` (via `sync_registry`), `call_tool` passes it to `_executor` $\rightarrow$ `ToolExecutor.execute`. Because the dynamic skill is not in `agent.spec`, `ToolExecutor.execute` falls through to `DynamicToolRegistry.execute_sync`, executing uncontained code outside `AgentRuntime.execute`!

#### Question 4: Plane-A Routing Authority (`core/routing.py`)
**Verdict:** **AUTHORITY INVENTORY IS SOUND AND VERIFIED; ZERO UNCATALOGUED PRE-LLM KEYWORD TRAPS REMAIN.**

- All 23 records in `ROUTERS` (`core/routing.py:162-339`) were reconciled against live code:
  - `A1` (`/sessions`, `/resume`, `/new`): Anchored syntax in `text_turn_controller.py:174`.
  - `A2` (Plugin dispatch): Delegated hook in `text_turn_controller.py:280`.
  - `A3` (Modal reply modes): Owned state in `text_turn_controller.py:284-295`.
  - `A4` (Offline/online modes): Word-bounded phrases in `text_turn_controller.py:298, 310`.
  - `A5` (Skill creation goal): Anchored regex in `main.py:_extract_skill_creation_goal`.
  - `A6` (Dynamic skill match): Converted to exact trigger/alias/name match in `dynamic_registry.py:394-478`.
  - `A7` (Email/calendar imperative): Anchored syntax in `text_turn_controller.py:394-445, 677`.
  - `A7b` (Daily briefing): Word-bounded phrases in `main.py:_looks_like_daily_briefing_request`.
  - `A8` (Presentation/sheet/web/code builders): Word-bounded verb + software noun in `main.py:430-489` and `text_turn_controller.py:159-165`.
  - `A9` / `A9b` (System diagnostics / Auto-heal): Word-bounded phrases in `text_turn_controller.py:761, 870`.
  - `A10` (Volume control): Word-bounded phrases in `text_turn_controller.py:983`.
  - `A11` (Open app): Exact alias matching via `routing.exact_alias` in `text_turn_controller.py:1050`.
  - `A12` (Instagram source): Owned channel state in `text_turn_controller.py:1066`.
  - `A13` (Meeting mode stop): Exact phrasing set in `text_turn_controller.py:1073`.
  - `A14` (Attention response): Owned window state in `core/attention_flow.py:125`.
  - `A15` (Task preview): Domain classification deleted; returns neutral 4-step placeholder in `main.py:684-699`.
  - `A16` (Brahma Connect): Phone word plus live paired device check in `core/device_flow.py:58-95`.
  - `A16b` (Active device command rewrite): Owned active device check in `smart_device_manager.py:305`.
  - `A17` (Smart home pre-LLM matcher): **Deleted entirely** in Phase 0; `smart_home_control` is exclusively a Plane-B tool choice.
  - `A18` (Screen inspection request): Word-bounded screen request in `main.py:_looks_like_screen_request`.
  - `A20` / `A21` (`intent_dispatcher` / `execution_reflection`): Verified completely dormant; tests enforce no app imports.
- **Negative Test Coverage:** All 105 tests in `tests/test_routing_authority.py` pass cleanly. No uncatalogued pre-LLM regex/keyword traps exist in `main.py` or `core/text_turn_controller.py`.

#### Question 5: Thread Safety in `ToolIndex` & `tool_bridge`
**Verdict:** **CRITICAL SQLITE THREAD CONFINEMENT BUG FOUND IN `_Fts5Backend`; RACE CONDITIONS IN `ToolExecutor` CALL TRACKING.**

- **The `_Fts5Backend` Thread Sharing Bug:**
  - `_Fts5Backend.build()` (`core/tool_index.py:504`) initializes SQLite with:
    ```python
    conn = sqlite3.connect(":memory:")
    ```
  - In Python's `sqlite3`, `check_same_thread=True` by default.
  - When `ToolIndex.rebuild()` is called on the GUI main thread (or during `main.py:prime()`), `self._conn` is permanently bound to the main thread.
  - When a subsequent search is performed from an `AgentLoop` worker thread or an asyncio worker thread (via `asyncio.to_thread`), `_Fts5Backend.rank()` executes `self._conn.execute(...)`.
  - **Observed Behavior:** `self._conn.execute(...)` raises:
    `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread.`
  - In lines 538–540, `_Fts5Backend.rank` catches `sqlite3.Error`, writes a debug log, and **silently returns `[]`**.
  - **Empirical Proof:** When queried across threads, `idx._fts.rank('test', 5)` returned `[]`, while returning hits in the originating thread. The primary BM25 keyword engine silently dies across threads, leaving retrieval solely dependent on n-grams and embeddings!
- **`ToolExecutor` State Race Condition:**
  - In `core/tool_exec.py:44-54`, `self._bridge_depth` and `self._recent_calls` are updated without synchronization. Concurrent `call_tool` invocations across worker threads race on `list.append()`, `list.count()`, and slice truncation.
- **Import-Time Side Effect in `workspace_store.py`:**
  - `workspace_store.py:542` instantiates `_STORE = WorkspaceStore()` at module level.
  - This executes `PRAGMA journal_mode=WAL` against `%LOCALAPPDATA%\BrahmaAI\workspace_store.sqlite3` during import of `ui.py` or `main.py`.
  - If the database file is locked or malformed, importing `main.py` crashes unconditionally with `sqlite3.DatabaseError: database disk image is malformed`.

---

## 4. Detailed Findings

### [WP-02-F01] Dynamic Tool Registry Fallback Bypasses `AgentRuntime` Permission Boundary
- **Severity:** `Critical`
- **Category:** `Security` / `Architecture/Invariant`
- **Location:** [core/tool_exec.py:L120-L144](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py#L120-L144) (`ToolExecutor.execute`) and [core/text_turn_controller.py:L352-L390](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L352-L390) (`TextTurnController.on_text_command`)
- **Evidence & Mechanism:**
  When `ToolExecutor.execute` receives a tool call for a tool not registered in `agent.spec`, lines 126–130 invoke `DynamicToolRegistry.execute_sync`:
  ```python
  if name and DynamicToolRegistry.has_tool(name):
      loop = asyncio.get_event_loop()
      run_res = await loop.run_in_executor(
          None, lambda: DynamicToolRegistry.execute_sync(name, args or {})
      )
  ```
  `DynamicToolRegistry.execute_sync` (`core/dynamic_registry.py:595-613`) delegates to `DynamicSkill.execute_sync` (`core/dynamic_registry.py:76-94`). Because codebase features in `features/*.py` have `is_synthetic = False`, lines 86–94 dynamically import the file using `importlib.util` and execute `module.execute(**kwargs)` in-process on the host.
  Similarly, `core/text_turn_controller.py:359` invokes `DynamicToolRegistry.execute_sync` directly when `find_matching_skill` matches a user utterance.
- **Impact:**
  Violates Invariant **I-01** ("Every tool call across CLI, GUI, Voice, API, and Outbound MCP must route through `AgentRuntime.execute`"). Dynamic skills in `features/` run with zero permission policy checks (`agent_permissions.json`), zero path confinement, zero headless rules, and zero interactive human confirmations. A prompt injection or compromised model turn can invoke forged or dynamic skills with full local user privileges.
- **Recommended Fix:**
  Wrap all dynamic skills in standard `ToolSpec` definitions registered under `tool_class="exec"` (or classified dynamically), with `tags=("dynamic_skill",)`. Route all dynamic skill invocations strictly through `AgentRuntime.execute(name, args, ...)`. Delete lines 120–144 of `core/tool_exec.py` and replace with a standard spec lookup. Remove the direct execution in `core/text_turn_controller.py:359` in favor of standard turn controller delegation.

---

### [WP-02-F02] SQLite In-Memory Connection in `_Fts5Backend` Fails Silently Across Threads
- **Severity:** `Critical`
- **Category:** `Concurrency/Threading` / `Correctness/Bug`
- **Location:** [core/tool_index.py:L504](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_index.py#L504) (`_Fts5Backend.build`)
- **Evidence & Mechanism:**
  `_Fts5Backend.build()` creates an in-memory SQLite connection via:
  ```python
  conn = sqlite3.connect(":memory:")
  ```
  In Python, `sqlite3.connect` defaults to `check_same_thread=True`. `ToolIndex.rebuild()` is called during startup on the GUI main thread, binding `self._conn` to thread ID $T_1$. When an agent turn executes on a worker thread ($T_2$) or asyncio thread ($T_3$), `ToolIndex.search()` invokes `_Fts5Backend.rank()` on $T_2$.
  SQLite throws `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`.
  Lines 538–540 catch `sqlite3.Error` and return `[]`:
  ```python
  except sqlite3.Error as exc:
      logger.debug("[ToolIndex] FTS5 query failed (%s) for %r", exc, query)
      return []
  ```
  Verified via diagnostic script: `idx._fts.rank('test', 5)` returns hits on the creator thread, but returns `[]` on any worker thread.
- **Impact:**
  BM25 keyword search silently breaks whenever queries originate from worker threads (GUI agent turns, CLI runs, or asyncio voice tasks). The index loses its primary exact-keyword retrieval backend, leading to severe retrieval degradation, miss of exact tool names, and erratic ranking behavior.
- **Recommended Fix:**
  Add `check_same_thread=False` to the connection string:
  ```python
  conn = sqlite3.connect(":memory:", check_same_thread=False)
  ```
  Because `ToolIndex.rebuild()` and `ToolIndex.search()` are already serialized behind `self._lock` (`threading.RLock`), sharing the in-memory connection across threads with `check_same_thread=False` is fully thread-safe. Add an explicit cross-thread assertion in `tests/test_tool_index.py` verifying non-empty FTS5 results from a spawned worker thread.

---

### [WP-02-F03] `desktop_control` Executes LLM-Generated Python Code via `exec()` Under Default-Allowed `device` Class
- **Severity:** `High`
- **Category:** `Security` / `Architecture/Invariant`
- **Location:** [core/desktop_tools.py:L101](file:///C:/Users/Admin/Brahma-Ai-Evo/core/desktop_tools.py#L101) (`WAVE3["desktop_control"]`) and [actions/desktop.py:L89-L94, L397-L414](file:///C:/Users/Admin/Brahma-Ai-Evo/actions/desktop.py#L89-L94) (`desktop_control`)
- **Evidence & Mechanism:**
  In `core/desktop_tools.py:101`, `desktop_control` is assigned `("device", False)`.
  In `agent/permissions.py:DEFAULT_POLICY["classes"]`, `"device"` is mapped to `ALLOW`.
  When `desktop_control` is called with `action="task"` (or with a natural language task description in `task`), lines 406–407 execute:
  ```python
  code = _ask_gemini_for_desktop_action(actual_task)
  return _execute_generated_code(code, player=player)
  ```
  `_execute_generated_code` (`actions/desktop.py:85-94`) compiles and runs the LLM output:
  ```python
  sandbox = _build_sandbox()
  exec(compile(code, "<brahma_desktop>", "exec"), sandbox)
  ```
  The "sandbox" in lines 55–73 injects live host instances of `ctypes`, `winreg`, `shutil`, `Path`, and `pyautogui`.
- **Impact:**
  An autonomous agent or model call can generate and execute arbitrary Python code utilizing `ctypes` and `shutil` with zero human confirmation, completely bypassing the `exec` policy class (`exec` defaults to `ASK`). This represents an arbitrary code execution vector hidden under the benign-looking `"device"` permission class.
- **Recommended Fix:**
  Add a classifier for `desktop_control` in `core/desktop_tools.py:CLASSIFIERS`:
  ```python
  def _classify_desktop_control(args: Mapping[str, Any]) -> str:
      action = str((args or {}).get("action") or "").strip().lower()
      task = str((args or {}).get("task") or "").strip()
      if action == "task" or task:
          return "exec"
      return "device"
  ```
  Furthermore, enforce `requires_approval=True` whenever dynamic code execution is requested, or route the code execution through `agent.process.run_captured` inside an isolated sandbox rather than executing `exec()` directly in the host process.

---

### [WP-02-F04] Module-Scope Instantiation of `WorkspaceStore` in `workspace_store.py` Violates Invariant I-26
- **Severity:** `High`
- **Category:** `Architecture/Invariant` / `Correctness/Bug`
- **Location:** [workspace_store.py:L542](file:///C:/Users/Admin/Brahma-Ai-Evo/workspace_store.py#L542) (`_STORE = WorkspaceStore()`), [ui.py:L49](file:///C:/Users/Admin/Brahma-Ai-Evo/ui.py#L49), and [main.py:L41](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L41)
- **Evidence & Mechanism:**
  `workspace_store.py:542` executes `_STORE = WorkspaceStore()` at top-level module scope upon import.
  In `WorkspaceStore.__init__` (`lines 82, 92`), it executes:
  ```python
  conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
  conn.execute("PRAGMA journal_mode=WAL;")
  ```
  `ui.py:49` imports `workspace_store`, and `main.py:41` imports `BrahmaUI` from `ui.py`.
  When `workspace_store.sqlite3` in `%LOCALAPPDATA%\BrahmaAI` is locked, corrupted, or in a malformed WAL state, merely importing `main.py` raises:
  `sqlite3.DatabaseError: database disk image is malformed`.
  This caused 5 test failures in `tests/test_routing_authority.py` during our audit when imported without overriding `%LOCALAPPDATA%`.
- **Impact:**
  Violates Invariant **I-26** ("No import-time side effects: importing a UI/core module must not start a thread, open a socket, or read user data"). Prevents headless CLI tools, diagnostic harnesses, and unit tests from importing `main.py` if the user's local database is in an inconsistent state.
- **Recommended Fix:**
  Convert `_STORE` to lazy initialization (`get_store()` singleton pattern) so the database connection and WAL pragma are opened only when store operations are actually invoked, not at module import time.

---

### [WP-02-F05] `ToolExecutor` Bridge Recursion Guard Is Ineffective and Recent Calls List Is Not Thread-Safe
- **Severity:** `Medium`
- **Category:** `Concurrency/Threading` / `Correctness/Bug`
- **Location:** [core/tool_exec.py:L36-L54](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py#L36-L54) (`ToolExecutor.execute_via_bridge`)
- **Evidence & Mechanism:**
  ```python
  def execute_via_bridge(self, name: str, args: dict) -> str:
      """Run a tool on behalf of the tool bridge's call_tool meta-tool."""
      self._bridge_depth += 1
      try:
          key = (name, str(args)[:200])
          self._recent_calls.append(key)
          if len(self._recent_calls) > 100:
              self._recent_calls = self._recent_calls[-30:]
          if self._recent_calls.count(key) > 5:
              return f"Tool '{name}' skipped to prevent loop (repeated calls detected)."
          return self.execute_sync(name, args, call_id="call_bridge_" + name)
      finally:
          self._bridge_depth -= 1
  ```
  1. `self._bridge_depth` is incremented and decremented, but never checked against any maximum depth (e.g. `if self._bridge_depth > 3: return "Max bridge recursion exceeded"`).
  2. `self._recent_calls` and `self._bridge_depth` are shared instance attributes on `ToolExecutor` without a lock. When `AgentLoop` dispatches parallel tool calls across multiple worker threads, concurrent calls to `execute_via_bridge` race on list mutation and slice assignment.
  3. Cycle loop detection only catches repetitions of the *identical* `key` > 5 times. An alternating cycle between two tools `(Tool_A, Tool_B, Tool_A, Tool_B...)` is not detected until 10 calls have executed.
- **Impact:**
  A malfunctioning agent or prompt-injected payload can drive deep recursive or cyclic `call_tool` chains. Thread races on `_recent_calls` can cause list index or mutation errors in multi-threaded turn dispatch.
- **Recommended Fix:**
  Add an explicit recursion depth ceiling check:
  ```python
  if self._bridge_depth > 3:
      return f"Tool '{name}' skipped: maximum call_tool recursion depth exceeded."
  ```
  Protect `_recent_calls` and `_bridge_depth` with a `threading.Lock()`.

---

### [WP-02-F06] `core/tool_policy.py` Capabilities and Shell Allowlist Engine Is Completely Disconnected from Production Dispatch
- **Severity:** `Medium`
- **Category:** `Dead/Duplicate Code` / `Security`
- **Location:** [core/tool_policy.py:L1-L213](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_policy.py#L1-L213)
- **Evidence & Mechanism:**
  `core/tool_policy.py` was created under Security Architecture Phase 2 (WS-5, SEC-10) to implement least-privilege capability mapping, shell command allowlisting (`ALLOWED_SHELL_COMMANDS`), blocked sensitive paths (`BLOCKED_SHELL_SUBSTRINGS`), and process-contained `execute_hardened_bash` and `execute_hardened_python_repl`.
  Static analysis across the repository reveals that **no production file in `core/`, `actions/`, `agent/`, or `main.py` imports or invokes `core/tool_policy.py`**. It is imported exclusively by `tests/test_tool_policy.py` and `tests/test_redteam.py`.
- **Impact:**
  Creates architectural dissonance: documentation and tests suggest that shell allowlisting and capability grants gate tool execution, whereas production dispatch uses `agent/permissions.py` and `agent.process.run_captured`. Security protections implemented in `tool_policy.py` are purely decorative in production.
- **Recommended Fix:**
  Either integrate `BLOCKED_SHELL_SUBSTRINGS` into `agent/permissions.py:DEFAULT_EXEC_DENY_PATTERNS` and delete `core/tool_policy.py`, or wire `evaluate_tool_call` into `AgentRuntime.execute` as part of the Phase 2 security roadmap.

---

### [WP-02-F07] Permissive Tool Classes for Destructive or System-Modifying Actions
- **Severity:** `Medium`
- **Category:** `Security` / `Architecture/Invariant`
- **Location:** [core/desktop_tools.py:L102, L151](file:///C:/Users/Admin/Brahma-Ai-Evo/core/desktop_tools.py#L102) (`computer_control`, `space_cleaner`)
- **Evidence & Mechanism:**
  - `computer_control`: Classified as `("device", False)`. Simulates low-level mouse and keyboard events (clicking, dragging, typing arbitrary text). `device` class defaults to `ALLOW` in `DEFAULT_POLICY`.
  - `space_cleaner`: Classified as `("write", False)` for action `purge`. Purges temporary files, browser cache, and user data. In `DEFAULT_POLICY`, `write` defaults to `ALLOW`.
- **Impact:**
  Destructive disk purge or arbitrary UI interaction proceeds without interactive user confirmation by default.
- **Recommended Fix:**
  Map `computer_control` to `tool_class="exec"` or enforce `requires_approval=True`. For `space_cleaner`, ensure `action="purge"` requires interactive confirmation (`requires_approval=True`).

---

### [WP-02-F08] Dormant Legacy Modules `intent_dispatcher.py` and `execution_reflection.py` Add Technical Debt
- **Severity:** `Low`
- **Category:** `Dead/Duplicate Code`
- **Location:** [core/intent_dispatcher.py:L1-L1051](file:///C:/Users/Admin/Brahma-Ai-Evo/core/intent_dispatcher.py#L1-L1051) and [core/execution_reflection.py:L1-L154](file:///C:/Users/Admin/Brahma-Ai-Evo/core/execution_reflection.py#L1-L154)
- **Evidence & Mechanism:**
  Both modules are catalogued as `DORMANT` in `core/routing.py` (`A20`, `A21`) and are strictly forbidden from being imported by app code (`tests/test_routing_authority.py:172`). Together they account for 1,205 lines of legacy code. `intent_dispatcher.py` attempts to import non-existent `core.omni_planner` inside a `try/except`.
- **Impact:**
  Bloats codebase size and misleads future contributors into attempting to re-wire unauthoritative pre-LLM intent classification.
- **Recommended Fix:**
  Schedule formal deprecation and deletion in Remediation Batch R-3 (Dead Code & Monolith Decomposition).

---

### [WP-02-F09] Documentation Drift in Declared Tool Counts
- **Severity:** `Informational`
- **Category:** `Doc Drift`
- **Location:** [main.py:L751, L2151](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L751) and `docs/FULL-SYSTEM-SCAN-AND-ANALYSIS-PLAN.md:WP-02`
- **Evidence & Mechanism:**
  Documentation states `main.py:TOOL_DECLARATIONS` contains 59 declarations. In code, the static list defines 59 declarations, but at line 2151 it appends 7 harness primitives (`TOOL_DECLARATIONS.extend(...)`), bringing the total length of `TOOL_DECLARATIONS` at runtime to 66.
- **Impact:**
  Minor confusion during audits and tool surface verification.
- **Recommended Fix:**
  Clarify in comments and architecture documentation that 59 is the count of native desktop declarations, while 66 is the post-import total including harness primitives.

---

## 5. Cross-Package Seams & Handoff Notes for Orchestrator

The findings in WP-02 directly intersect with several other Work Packages. The Orchestrator should coordinate the following seams during master synthesis (WP-13):

1. **Seam to WP-01 (Agentic Harness Core):**
   - `ToolIndex._fts` SQLite thread-sharing bug directly impacts `agent/mcp.py` and `AgentLoop`: when `AgentLoop` executes on a worker thread and calls `search_tools`, FTS5 fails silently unless `check_same_thread=False` is applied.
   - Dynamic skill execution fallback in `core/tool_exec.py:120-144` completely bypasses `agent/runtime.py:AgentRuntime.execute`. WP-01 auditors should verify `AgentRuntime.execute` interface requirements for dynamic specs.
2. **Seam to WP-03 (App Core & Session Lifecycle):**
   - `workspace_store.py:542` imports and instantiates the SQLite database at module level, violating I-26 and risking import crashes during test collection or headless runs. WP-03 auditors should review `workspace_store.py` lifecycle.
   - `main.py:prime()` calls `sync_all()`, which builds the index on the GUI main thread, triggering the cross-thread SQLite issue.
3. **Seam to WP-04 (Security Architecture & Skills Forge):**
   - `features/*.py` files are auto-imported and executed in-process by `DynamicToolRegistry` without using `core/skill_host.py` AppContainer sandbox. WP-04 auditors must verify if forged skills ever get isolated in production or if `skill_host.py` is only tested in isolation.
   - `core/tool_policy.py` is disconnected from production; WP-04 should evaluate whether to merge it into `agent/permissions.py` or discard it.
4. **Seam to WP-09 & WP-10 (Native Actions):**
   - `actions/desktop.py` (`desktop_control`) invokes dynamic LLM code generation and executes it via `exec()` in the host process with `ctypes`. WP-09 auditors should review `actions/desktop.py` security posture.

---

## 6. Prioritized Action Queue for This Subsystem

### 1. Immediate (P0 / P1) — Critical Vulnerabilities & Bug Fixes
- [ ] **[P0] Fix SQLite Thread-Sharing in `_Fts5Backend` ([WP-02-F02]):** In `core/tool_index.py:504`, change `sqlite3.connect(":memory:")` to `sqlite3.connect(":memory:", check_same_thread=False)`. Add a unit test in `tests/test_tool_index.py` asserting non-empty FTS5 search results from a separate spawned thread.
- [ ] **[P1] Close `DynamicToolRegistry` Policy Bypass ([WP-01-F01]):** Remove the fallback execution in `core/tool_exec.py:120-144` and `core/text_turn_controller.py:359`. Register dynamic skills into `agent.spec` as `ToolSpec` objects so that all dynamic invocations route through `AgentRuntime.execute`.
- [ ] **[P1] Secure `desktop_control` Execution Class ([WP-02-F03]):** Add `_classify_desktop_control` in `core/desktop_tools.py` that maps `action="task"` to policy class `"exec"` and enforces `requires_approval=True`.
- [ ] **[P1] Eliminate Import-Time Side Effect in `workspace_store.py` ([WP-02-F04]):** Refactor `_STORE = WorkspaceStore()` in `workspace_store.py:542` to a lazy `get_store()` getter to satisfy Invariant I-26.

### 2. Near-Term (P2) — Hardening & Concurrency Protection
- [ ] **[P2] Enforce Bridge Recursion Depth & Thread Safety ([WP-02-F05]):** Add a hard recursion ceiling check (`if self._bridge_depth > 3: return "Max recursion exceeded"`) and wrap `self._recent_calls` mutations behind a `threading.Lock()` in `core/tool_exec.py`.
- [ ] **[P2] Reclassify Permissive Destructive Actions ([WP-02-F07]):** Ensure `space_cleaner` with `action="purge"` requires interactive approval, and review `computer_control` policy classification.
- [ ] **[P2] Reconcile `core/tool_policy.py` ([WP-02-F06]):** Port `BLOCKED_SHELL_SUBSTRINGS` into `agent/permissions.py:DEFAULT_EXEC_DENY_PATTERNS` to make sensitive path protections active in production.

### 3. Cleanup / Refactor (P3) — Technical Debt Retirement
- [ ] **[P3] Delete Dormant Legacy Modules ([WP-02-F08]):** Remove `core/intent_dispatcher.py` (1,051 lines) and `core/execution_reflection.py` (154 lines) after updating tests in `tests/test_routing_authority.py`.
- [ ] **[P3] Update Documentation on Tool Counts ([WP-02-F09]):** Document the distinction between the 59 static native tool declarations and the 66 runtime declarations in `main.py`.
