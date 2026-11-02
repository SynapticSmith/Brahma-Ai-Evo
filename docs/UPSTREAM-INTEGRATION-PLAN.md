# Brahma-Ai-Evo: Upstream Integration Plan (Rev 2)

**Target:** Integrate upstream changes from [titechprabhasolutions/Brahma-Ai-Evo:main](https://github.com/titechprabhasolutions/Brahma-Ai-Evo) (**7 commits**, **+34,763 / -3,390 lines**, **114 files**) into our refactored, hardened architecture while strictly preserving our security, line-count, and single-runtime invariants.

**Status:** Planning & Mapping Document (Updated for all 7 upstream commits)  
**Created:** 2025-10-24 · **Amended:** 2026-11-02 (Post-Bridge v2 / Post-Security Phase 0 reconciliation)  
**Merge-Base:** `e0bd3c5a3a258cc1cf35014ae0a35c7b6b9db87f`  
**Our HEAD:** `4bcbc6b docs: reconcile doc drift across architecture, harness status, security roadmap, and bridge design records`  
**Upstream HEAD:** `bfb2868 Add public Privacy Policy for Google Play Store compliance`  
**Verified Test Baseline:** 888 passed / 1 skipped (offscreen); 460 passed / 1 skipped (`-k agent`)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State Analysis (All 7 Upstream Commits)](#2-current-state-analysis-all-7-upstream-commits)
3. [Architectural Invariants & Non-Negotiable Gates](#3-architectural-invariants--non-negotiable-gates)
4. [Integration Strategy & Decision Matrix](#4-integration-strategy--decision-matrix)
5. [Pre-Integration Checklist](#5-pre-integration-checklist)
6. [Phase 0: Preparation & Branch Setup](#6-phase-0-preparation--branch-setup)
7. [Phase 1: Low-Conflict Standalone Additions (Copy & Adapt)](#7-phase-1-low-conflict-standalone-additions-copy--adapt)
8. [Phase 2: Capability Ports Requiring Architecture Adaptation](#8-phase-2-capability-ports-requiring-architecture-adaptation)
9. [Phase 3: Surgical Fixes & Patches to Shared Seams](#9-phase-3-surgical-fixes--patches-to-shared-seams)
10. [Phase 4: Upstream Items Explicitly Rejected or Replaced](#10-phase-4-upstream-items-explicitly-rejected-or-replaced)
11. [Phase 5: Validation & Gate Verification](#11-phase-5-validation--gate-verification)
12. [Rollback Plan](#12-rollback-plan)
13. [Complete 114-File Tracking Matrix](#13-complete-114-file-tracking-matrix)
14. [Decision Log](#14-decision-log)

---

## 1. Executive Summary

### The Verdict: Selective Surgical Porting Only — Zero Wholesale Merge

Upstream has advanced across **7 commits** (+34,763 / -3,390 lines across 114 files). Concurrently, our repository has undergone foundational architectural evolution:
- **Security Phase 0 (100% Landed):** Uncontained SkillForge `execute_sync` removed; runtime `pip` removed; Gateway locked to `127.0.0.1` with paired-device bearer auth; plugins restricted to an opt-in allowlist; secrets moved behind Windows DPAPI (`core/secret_store.py`); insecure startup updaters deleted.
- **Harness & Boundary Correctness (Queue 1, 100% Landed):** 84 native actions migrated to canonical `ToolSpec`s in `core/desktop_tools.py` behind `AgentRuntime.execute`; `core/tool_exec.py` reduced to 147 lines with **0 `elif` branches**; `main.py` line ceiling ratcheted to **3,230 lines**; container behavioral confinement probe (`sandbox doctor`); read-only subagent fan-out (`run_subagent_batch`).
- **Bridge v2 (Queue 2, 100% Landed):** Greenfield `core/agent_bridge/` package implementing side-effect-free detectors, thread-safe atomic registry, adapters (MCP, local model, CLI agent with process-tree containment), durable artifact storage (`core/agent_bridge/artifacts.py`), and unified `AgentBridge` facade.

A blind `git merge upstream/main` would completely destroy these architectural boundaries: it would re-introduce unauthenticated LAN gateway routes, resurrect uncontained execution engines, overwrite DPAPI secrets storage, and explode the `main.py` line count by 2,300+ lines.

**Every upstream commit must be filtered through our ratified architecture:**
1. High-value isolated features (installer uninstaller, neural image generator, REA web cloner, audio stutter fixes, 12 project skill playbooks, privacy policy) are extracted and ported.
2. Capability tools must become canonical `ToolSpec`s registered in `core/desktop_tools.py` gated by `AgentRuntime.execute`.
3. Uncontained parallel runners (`core/subagent_orchestrator.py`) and unauthenticated gateway auto-pairing in `9dd02cf` are **explicitly rejected**.

---

## 2. Current State Analysis (All 7 Upstream Commits)

### Repository Coordinates
```text
Base Commit:   e0bd3c5a3a258cc1cf35014ae0a35c7b6b9db87f (merge-base)
Upstream HEAD: bfb2868074c3fac1cbebf00e4d2318c926fa86d2 (2026-10-27)
Our HEAD:      4bcbc6b (2026-11-02, Queue 1 and Queue 2 complete)
```

### Cumulative Diff Statistics (`e0bd3c5..upstream/main`)
| Metric | Value |
| --- | ---: |
| Total Commits Ahead | **7** |
| Total Files Changed | **114** |
| Added Files (`A`) | **71** |
| Modified Files (`M`) | **42** |
| Deleted Files (`D`) | **1** (`config/.desktop_shortcut_created`) |
| Total Insertions | **+34,763** |
| Total Deletions | **-3,390** |

### Complete Upstream Commit Chronology

| # | Commit | Date | Author | Summary | Key File Touchpoints |
|---|---|---|---|---|---|
| **1** | `987f47d` | 2026-10-18 | GodxSuryaansh | **Major Feature Release:** Neural image generation, REA web cloning, autonomous evolution suite, sentinels, radar/space HUDs, UI contrast fixes. | 81 files: `actions/image_generator.py`, `actions/rea_mcp_client.py`, `actions/reverse_engineer.py`, `core/flowstate.py`, `core/net_sentinel.py`, `core/shopping_agent.py`, `ui.py`, `main.py` |
| **2** | `c5e8162` | 2026-10-18 | GodxSuryaansh | **Installer Uninstaller Bundle:** Bundle native `Uninstall.exe` and `uninstall.cmd` into `BrahmaEvo_Setup` installer package. | 3 files: `installer/Uninstall.spec`, `installer/BrahmaEvo.spec`, `installer/BrahmaEvo_Setup.spec` |
| **3** | `c8d9c0f` | 2026-10-18 | GodxSuryaansh | **Multi-Platform Installer:** Universal macOS/Linux installer scripts and GitHub Actions multi-platform build workflow. | 3 files: `installer/install.sh`, `installer/uninstall.sh`, `.github/workflows/build-all-platforms.yml` |
| **4** | `9dd02cf` | 2026-10-26 | GodxSuryaansh | **Real-Time Voice Overhaul & Subagent Tools:** Overhaul real-time voice pipeline; dynamic chunk sizing (`blocksize=0`); echo barge-in gating; live voice engine; Claude tools; skills engine; subagent orchestrator; voice dashboard; 12 skill playbooks. | 31 files: `core/live_voice_engine.py`, `core/claude_tools.py`, `core/skills_engine.py`, `core/subagent_orchestrator.py`, `dashboard/static/voice.html`, `skills/*/SKILL.md` (12 files), `main.py` (+653/-112) |
| **5** | `b3c4160` | 2026-10-26 | GodxSuryaansh | **Skills Installer Bundling:** Bundle `skills` directory and update `build_all.ps1` for uninstaller integration. | 2 files: `build_all.ps1`, `installer/BrahmaEvo.spec` |
| **6** | `62b1591` | 2026-10-26 | GodxSuryaansh | **OpenRouter Dynamic Key & Fallback:** OpenRouter client dynamic key reloading, model pool fallback, and error handling. | 3 files: `or_client.py` (+140/-76), `llm_client.py`, `main.py` |
| **7** | `bfb2868` | 2026-10-27 | GodxSuryaansh | **Google Play Privacy Policy:** Public privacy policy document for Google Play Store compliance. | 1 file: `PRIVACY_POLICY.md` (+52 lines) |

---

## 3. Architectural Invariants & Non-Negotiable Gates

Any porting batch must respect the following invariants ratified in `docs/ARCHITECTURE-SPEC.md`:

1. **Single Runtime Enforcement Boundary (`I-01`, `D3`, `ADR-018`):** Every model-invokable capability must register as a `ToolSpec` and dispatch through `AgentRuntime.execute`. No tool or subagent may execute ungated.
2. **Line-Count & Ratchet Invariants (`tests/test_architecture_invariants.py`):**
   - `main.py`: **$\le 3,230$ lines** (never increase; upstream's +2,300 lines in `main.py` cannot be merged directly).
   - `core/tool_exec.py`: **$\le 147$ lines** and **exactly 0 `elif` branches**. All native tools live in `core/desktop_tools.py`.
   - `ui.py`: **$\le 827$ lines** (facade over decomposed `core/ui_*` modules).
3. **Pure Stdlib Harness (`I-15`):** The `agent/` package remains stdlib-only.
4. **Secrets at Rest via Windows DPAPI (`WS-7`):** API keys (`config/api_keys.json`) and Fernet keys (`smart_home/storage.py`) must remain encrypted with user-scoped Windows DPAPI via `core/secret_store.py`. No plaintext key storage may be re-introduced.
5. **Gateway Security (`WS-8`):** Default host is `127.0.0.1`, `enabled=False`. All control routes require paired-device bearer authentication. No auto-pairing or `/gateway/execute` route.
6. **No Subagent Duplication (`I-08`, `C-3`):** Subagents execute strictly through `run_subagent` (sequential mutation) or `run_subagent_batch` (read-only parallel fan-out). Upstream's `core/subagent_orchestrator.py` is forbidden.
7. **Gemini Live Setup Budget (`I-19`, `ADR-021`):** Advertised tools must not exceed `budget()`. Deferred tools must use `call_tool(...)` templates.

---

## 4. Integration Strategy & Decision Matrix

| Upstream Feature Area | Primary Commits | Verdict | Porting / Adaptation Strategy |
|---|---|---|---|
| **Privacy Policy** | `bfb2868` | **ACCEPT (Direct Copy)** | Copy `PRIVACY_POLICY.md` into repository root. Zero conflict. |
| **Installer Uninstaller & Shell Scripts** | `c5e8162`, `c8d9c0f`, `b3c4160` | **ACCEPT (Selective Port)** | Port `Uninstall.spec`, `uninstall.cmd`, `install.sh`, `uninstall.sh`. Adapt `build_all.ps1` without breaking `requirements-build.txt` reproducibility. |
| **Neural Image Generation** | `987f47d` | **ADAPT (ToolSpec)** | Port `actions/image_generator.py` into a canonical `ToolSpec` (`tool_class="network"`, `requires_approval=False`) registered in `core/desktop_tools.py`. Route via `core/llm_gateway.py`. |
| **REA Web Cloning & Reverse Engineering** | `987f47d` | **ADAPT (ToolSpec)** | Port `actions/rea_mcp_client.py` and `actions/reverse_engineer.py` as `ToolSpec`s (`tool_class="network"` or `"write"`). |
| **Project Skill Playbooks (12 Skills)** | `9dd02cf` | **ACCEPT (Skills Library)** | Upstream added 12 `skills/*/SKILL.md` files. Drop into project skills `.brahma/skills/` or Skills Library under Decision `D42`. Instructional-only, fully compatible. |
| **Audio Pipeline & Echo Barge-In Fixes** | `9dd02cf` | **ADAPT (Surgical Patch)** | Port PortAudio `RawOutputStream(blocksize=0)` dynamic chunk sizing, barge-in gating in `core/echo.py`, and interrupted turn suppression into `main.py` / `core/session_lifecycle.py` without blowing line ratchets. |
| **OpenRouter Dynamic Key & Fallback** | `62b1591` | **ADAPT (DPAPI Aware)** | Port model fallback pool and retry logic into `or_client.py`. Ensure keys are loaded dynamically via `core/secret_store.py:load_api_keys()` (DPAPI). |
| **Sentinels & Cleaners (Game, Net, Space, Study)** | `987f47d` | **ADAPT (Component Modules)** | Copy `core/game_sentinel.py`, `core/net_sentinel.py`, `core/space_cleaner.py`, `core/study_guard.py` and their HTML assets. Wrap entry points in `ToolSpec`s. |
| **Autonomous Evolution & Reflection** | `987f47d` | **SELECTIVE (Audit First)** | Review `core/execution_reflection.py` and `core/mem_reduct.py`. Reject monolithic state replacers (`core/omni_planner.py`). |
| **Upstream Subagent Orchestrator** | `9dd02cf` | **REJECT** | `core/subagent_orchestrator.py` introduces a redundant, unconfined subagent execution loop that bypasses `AgentRuntime` and violates `I-08`. |
| **Gateway Auto-Pairing Bypass** | `9dd02cf` | **REJECT** | Upstream disabled pairing validation in `gateway/server.py`. Conflicts directly with `WS-8` security hardening. |
| **Monolithic `main.py` & `ui.py` Regrowth** | `987f47d`, `9dd02cf` | **REJECT (Wholesale Merge)** | Upstream injects thousands of lines into monolithic `main.py` and `ui.py`. Adapt all UI hooks into decomposed `core/ui_*` modules. |

---

## 5. Pre-Integration Checklist

Before executing any porting batch:
- [x] **Working tree clean**: On `main` branch, `git status` clean.
- [x] **Upstream fetched**: `git fetch upstream --prune` completed.
- [x] **Regression test baseline established**:
  - `py -3.11 -m ruff check .` → Clean.
  - `$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q` → **888 passed, 1 skipped**.
  - `py -3.11 -m pytest tests -q -k agent` → **460 passed, 1 skipped**.
  - `py -3.11 -m pytest tests/test_architecture_invariants.py -v` → **5 passed** (`main.py` 3,230 lines $\le$ 3,230 ceiling; 0 `elif`s).
- [ ] **Isolated integration branch created**: `git checkout -b integration/upstream-phase1`.
- [ ] **Backup tag created**: `git tag backup/pre-upstream-integration-$(date +%Y%m%d)`.

---

## 6. Phase 0: Preparation & Branch Setup

```powershell
# 1. Ensure latest upstream commits are present in object store
git fetch upstream --prune

# 2. Create isolated work branch
git checkout -b integration/upstream-phase1

# 3. Create pre-integration backup tag
git tag backup/pre-upstream-port-20261102
```

---

## 7. Phase 1: Low-Conflict Standalone Additions (Copy & Adapt)

**Objective:** Import self-contained files that require zero or minimal architecture adaptation.

### Batch 1.1: Documentation & Compliance (Commit `bfb2868`)
- Copy `PRIVACY_POLICY.md` into repository root.
- Verify no proprietary credentials or invalid links.

### Batch 1.2: Multi-Platform Installers & Packaging (Commits `c5e8162`, `c8d9c0f`, `b3c4160`)
- **Files to copy:**
  - `installer/Uninstall.spec`
  - `installer/uninstall_wizard.py`
  - `installer/uninstall.cmd`
  - `installer/install.sh`
  - `installer/uninstall.sh`
  - `.github/workflows/build-all-platforms.yml` (evaluate against `.github/workflows/ci.yml`)
- **Adaptation:**
  - In `build_all.ps1`: Integrate uninstaller compilation (`Uninstall.exe`) while preserving Python 3.11 requirement and SHA-256 manifest generation (`dist/SHA256SUMS.txt`).

### Batch 1.3: Project Skills Library Playbooks (Commit `9dd02cf`)
- **12 New Skill Folders to Copy into `skills/` (and `.brahma/skills/`):**
  - `skills/api-mock-engine/SKILL.md`
  - `skills/browser-automation/SKILL.md`
  - `skills/code-review/SKILL.md`
  - `skills/database-architect/SKILL.md`
  - `skills/docker-devops/SKILL.md`
  - `skills/fullstack-scaffolder/SKILL.md`
  - `skills/git-workflow/SKILL.md`
  - `skills/github-automation/SKILL.md`
  - `skills/performance-profiler/SKILL.md`
  - `skills/security-auditor/SKILL.md`
  - `skills/test-driven-development/SKILL.md`
  - `skills/web-scraper-stealth/SKILL.md`
- **Validation:** Verify skills parse cleanly via `agent.skills` parser without schema violations.

### Batch 1.4: Standalone HTML / Three.js Visual Assets (Commit `987f47d`)
- `assets/radar/index.html` (NEW)
- `assets/space_cleaner/index.html` (NEW)
- `dashboard/static/voice.html` (NEW)

---

## 8. Phase 2: Capability Ports Requiring Architecture Adaptation

**Objective:** Port external actions and sentinels, adapting them into canonical `ToolSpec`s registered in `core/desktop_tools.py` behind `AgentRuntime.execute`.

### Batch 2.1: Neural Image Generator (Commit `987f47d`)
- **Source:** `actions/image_generator.py`, `tests/test_image_generator.py`.
- **Adaptation Requirements:**
  - Remove direct LLM API calls; route through `core/llm_gateway.py`.
  - Register as `ToolSpec(name="generate_image", tool_class="network", requires_approval=False)` in `core/desktop_tools.py`.
  - Add to `ToolIndex` with descriptive keywords.

### Batch 2.2: REA Web Cloning & Reverse Engineering (Commit `987f47d`)
- **Source:** `actions/rea_mcp_client.py`, `actions/reverse_engineer.py`, `tests/test_reverse_engineer.py`.
- **Adaptation Requirements:**
  - Wrap in `ToolSpec`s with appropriate risk classes (`"network"` for reading/fetching, `"write"` for saving extracted code/assets).
  - Enforce path resolution inside `runtime.workspace` to prevent workspace breakout.

### Batch 2.3: Audio Pipeline Stutter Fixes & Echo Barge-In Gating (Commit `9dd02cf`)
- **Source:** Modifications in `core/echo.py` and voice loop in `main.py`.
- **Fixes to Extract:**
  - PortAudio `RawOutputStream(blocksize=0)`: Allows dynamic audio buffer chunk sizing to prevent buffer underrun micro-loops.
  - Barge-in gating in `core/echo.py`: Gate acoustic energy detection while TTS speaker is active to prevent self-interruption loops.
  - Interrupted turn suppression flag: Drop residual audio frames from previous turn after interruption.
- **Line Count Guard:** Apply surgical edits to `core/session_lifecycle.py` and `core/echo.py` rather than inflating `main.py`.

### Batch 2.4: OpenRouter Dynamic Key Reload & Model Pool Fallback (Commit `62b1591`)
- **Source:** `or_client.py`, `llm_client.py`.
- **Adaptation Requirements:**
  - Adopt dynamic key reloading using `core/secret_store.py:load_api_keys()` (DPAPI-protected).
  - Adopt model pool fallback hierarchy (e.g. falling back to secondary fast models on 429/500 errors).
  - Respect `core/provider_router.py` kill switches.

### Batch 2.5: Sentinel Modules (Commit `987f47d`)
- **Source:** `core/game_sentinel.py`, `core/net_sentinel.py`, `core/space_cleaner.py`, `core/study_guard.py`.
- **Adaptation Requirements:**
  - Port core monitoring logic into `actions/` or `core/` as pure services.
  - Wire UI widgets into `core/ui_*` component modules, not monolithic `ui.py`.

---

## 9. Phase 3: Surgical Fixes & Patches to Shared Seams

**Priority:** CRITICAL | **Risk:** VERY HIGH | **Rule:** Single-file targeted edits only.

### 3.1 `core/prompt.txt`
- Semantically merge upstream's prompt guidance for Reverse Engineering and Image Generation into our structured prompt brief.
- Preserve all existing guidance on tool discovery, memory, safety, and local models.

### 3.2 `config/app_settings.json`
- Merge new default configuration keys introduced by upstream (e.g. image generation model defaults, sentinel thresholds).
- Preserve existing keys: gateway loopback defaults, DPAPI settings, trusted workspaces.

### 3.3 `core/dynamic_registry.py` & `core/learned_rules.py`
- Extract `detect_and_learn_conversational_rule()` from upstream `core/learned_rules.py`.
- In `core/dynamic_registry.py`: Review stem expansion improvements without degrading the strict `1008` Gemini Live tool budget.

---

## 10. Phase 4: Upstream Items Explicitly Rejected or Replaced

To protect the architecture, the following upstream additions are **permanently rejected** from being merged:

1. **`core/subagent_orchestrator.py` (`9dd02cf`):**
   - *Why rejected:* Spawns arbitrary subprocesses and uncontained agent loops. Replaced by ratified `run_subagent` (sequential mutation) and `run_subagent_batch` (read-only parallel fan-out under `AgentRuntime`).
2. **Gateway Auto-Pairing Bypass (`9dd02cf`):**
   - *Why rejected:* Upstream commented out device verification in `gateway/server.py`. Replaced by `WS-8` paired-device authentication.
3. **Monolithic `main.py` & `ui.py` Regrowth (`987f47d`, `9dd02cf`):**
   - *Why rejected:* Upstream merged 2,300 lines directly into `main.py` and 1,700 lines into `ui.py`. Directly violates CI ratchets (`main.py` $\le$ 3,230 lines; `core/tool_exec.py` 0 `elif` branches).
4. **Uncontained Code Execution via `agent_task` / `executor.py` (`987f47d`):**
   - *Why rejected:* Legacy autonomous files were deleted in Batch B4. Any autonomous capability must route through `AgentLoop`.

---

## 11. Phase 5: Validation & Gate Verification

Every porting batch must pass all repository verification gates:

```powershell
# 1. Code formatting & linting
py -3.11 -m ruff check .

# 2. Architecture invariants and line-count ratchets
py -3.11 -m pytest tests/test_architecture_invariants.py -v

# 3. Agentic harness & bridge regression suite
py -3.11 -m pytest tests -q -k agent

# 4. Full offscreen desktop test suite
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
```

---

## 12. Rollback Plan

If any porting batch introduces regressions:
1. `git checkout main && git branch -D integration/upstream-phaseX`
2. If working tree was affected: `git reset --hard backup/pre-upstream-port-20261102`
3. Never force-push or rebase `main`.

---

## 13. Complete 114-File Tracking Matrix

Status values: `PENDING` · `PORTED` · `ADAPTED` · `REJECTED` · `SUPERSEDED`

### 13.1 Added Files in Upstream (`A`, 71 Files)

| # | File | Upstream Commit | Component | Strategy | Status | Notes |
|---|---|---|---|---|---|---|
| 1 | `.github/workflows/build-all-platforms.yml` | `c8d9c0f` | CI/CD | ADAPT | PENDING | Evaluate vs `.github/workflows/ci.yml` |
| 2 | `PRIVACY_POLICY.md` | `bfb2868` | Compliance | COPY | PENDING | Zero conflict; direct copy |
| 3 | `actions/game_sentinel.py` | `987f47d` | Sentinel | ADAPT | PENDING | Wrap as service/tool |
| 4 | `actions/image_generator.py` | `987f47d` | Capability | ADAPT | PENDING | Port to `ToolSpec` in `desktop_tools.py` |
| 5 | `actions/net_sentinel.py` | `987f47d` | Sentinel | ADAPT | PENDING | Wrap as service/tool |
| 6 | `actions/rea_mcp_client.py` | `987f47d` | Capability | ADAPT | PENDING | Wrap as `ToolSpec` |
| 7 | `actions/reverse_engineer.py` | `987f47d` | Capability | ADAPT | PENDING | Wrap as `ToolSpec` |
| 8 | `actions/study_guard.py` | `987f47d` | Sentinel | ADAPT | PENDING | Wrap as service/tool |
| 9 | `assets/radar/index.html` | `987f47d` | Assets | COPY | PENDING | Standalone Three.js radar asset |
| 10 | `assets/space_cleaner/index.html` | `987f47d` | Assets | COPY | PENDING | Standalone space cleaner asset |
| 11 | `brahma_connect/gateway/mobile_portal.py` | `987f47d` | Gateway | ADAPT | PENDING | Ensure route auth (`WS-8`) is applied |
| 12 | `config/mcp_servers.json` | `9dd02cf` | MCP Config | EVALUATE | PENDING | Check vs `app_settings.json` and ADR-020 |
| 13 | `core/anticipatory_watcher.py` | `987f47d` | Evolution | ADAPT | PENDING | Background observer |
| 14 | `core/claude_tools.py` | `9dd02cf` | Tools | ADAPT | PENDING | Integrate into `desktop_tools.py` |
| 15 | `core/execution_reflection.py` | `987f47d` | Evolution | ADAPT | PENDING | High-value self-critique module |
| 16 | `core/flowstate.py` | `987f47d` | Feature | ADAPT | PENDING | Productivity monitor |
| 17 | `core/game_sentinel.py` | `987f47d` | Sentinel | ADAPT | PENDING | Service module |
| 18 | `core/intent_dispatcher.py` | `987f47d` | Routing | EVALUATE | PENDING | Compare with `provider_router.py` |
| 19 | `core/live_voice_engine.py` | `9dd02cf` | Voice | ADAPT | PENDING | Extract streaming fixes |
| 20 | `core/mcp_manager.py` | `9dd02cf` | MCP | SUPERSEDED | SUPERSEDED | We implemented `core/mcp_manager.py` + refresh (B22) |
| 21 | `core/mem_reduct.py` | `987f47d` | Evolution | ADAPT | PENDING | Memory reduction utility |
| 22 | `core/memory_cortex.py` | `987f47d` | Memory | EVALUATE | PENDING | Compare with `agent/session.py` |
| 23 | `core/net_sentinel.py` | `987f47d` | Sentinel | ADAPT | PENDING | Network guard core |
| 24 | `core/omni_planner.py` | `987f47d` | Planning | REJECT | REJECT | Conflicts with `AgentLoop` |
| 25 | `core/radar_hud.py` | `987f47d` | HUD | ADAPT | PENDING | Adapt into `core/ui_*` overlay |
| 26 | `core/shopping_agent.py` | `987f47d` | Feature | ADAPT | PENDING | Wrap as `ToolSpec` |
| 27 | `core/skills_engine.py` | `9dd02cf` | Skills | SUPERSEDED | SUPERSEDED | Replaced by `agent.skills` / Skills Library (D42) |
| 28 | `core/space_cleaner.py` | `987f47d` | Feature | ADAPT | PENDING | Disk cleanup helper |
| 29 | `core/space_cleaner_hud.py` | `987f47d` | HUD | ADAPT | PENDING | Adapt into `core/ui_*` overlay |
| 30 | `core/study_guard.py` | `987f47d` | Feature | ADAPT | PENDING | Focus monitor |
| 31 | `core/subagent_orchestrator.py` | `9dd02cf` | Subagents | REJECT | REJECT | Conflicts with `I-08` / `run_subagent_batch` |
| 32 | `dashboard/static/voice.html` | `9dd02cf` | Dashboard | COPY | PENDING | Standalone dashboard voice interface |
| 33 | `features/ascii_banner.py` | `987f47d` | Skill | ADAPT | PENDING | Register via staging/forge rules |
| 34 | `features/ascii_banner/manifest.json` | `987f47d` | Skill | COPY | PENDING | Skill manifest |
| 35 | `features/ascii_banner/skill.py` | `987f47d` | Skill | COPY | PENDING | Skill code |
| 36 | `features/ascii_banner/test_cases.json` | `987f47d` | Skill | COPY | PENDING | Test cases |
| 37 | `features/flowstate.py` | `987f47d` | Feature | ADAPT | PENDING | Feature entry point |
| 38 | `features/game_sentinel.py` | `987f47d` | Feature | ADAPT | PENDING | Feature entry point |
| 39 | `features/net_sentinel.py` | `987f47d` | Feature | ADAPT | PENDING | Feature entry point |
| 40 | `features/shopping_agent.py` | `987f47d` | Feature | ADAPT | PENDING | Feature entry point |
| 41 | `features/study_guard.py` | `987f47d` | Feature | ADAPT | PENDING | Feature entry point |
| 42 | `installer/Uninstall.spec` | `c5e8162` | Installer | ADAPT | PENDING | Uninstaller build spec |
| 43 | `installer/install.sh` | `c8d9c0f` | Installer | COPY | PENDING | Linux/macOS install script |
| 44 | `installer/uninstall.cmd` | `c5e8162` | Installer | COPY | PENDING | Windows native uninstaller command |
| 45 | `installer/uninstall.sh` | `c8d9c0f` | Installer | COPY | PENDING | Linux/macOS uninstall script |
| 46 | `installer/uninstall_wizard.py` | `c5e8162` | Installer | ADAPT | PENDING | GUI uninstaller wizard |
| 47 | `skills/api-mock-engine/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 48 | `skills/browser-automation/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 49 | `skills/code-review/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 50 | `skills/database-architect/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 51 | `skills/docker-devops/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 52 | `skills/fullstack-scaffolder/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 53 | `skills/git-workflow/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 54 | `skills/github-automation/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 55 | `skills/performance-profiler/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 56 | `skills/security-auditor/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 57 | `skills/test-driven-development/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 58 | `skills/web-scraper-stealth/SKILL.md` | `9dd02cf` | Playbook | COPY | PENDING | Project skill playbook |
| 59 | `tests/test_ai_evolution.py` | `987f47d` | Test | ADAPT | PENDING | Adapt to harness |
| 60 | `tests/test_flowstate.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for flowstate |
| 61 | `tests/test_game_sentinel.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for game sentinel |
| 62 | `tests/test_image_generator.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for image generator |
| 63 | `tests/test_intent_dispatcher.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for dispatcher |
| 64 | `tests/test_mobile_connect_routing.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for mobile portal |
| 65 | `tests/test_net_sentinel.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for net sentinel |
| 66 | `tests/test_proactive_engine.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for proactive engine |
| 67 | `tests/test_reverse_engineer.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for REA cloner |
| 68 | `tests/test_shopping_agent.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for shopping agent |
| 69 | `tests/test_space_cleaner.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for space cleaner |
| 70 | `tests/test_study_guard.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for study guard |
| 71 | `tests/test_ui_upgrades.py` | `987f47d` | Test | ADAPT | PENDING | Test suite for UI contrast |

---

### 13.2 Modified Files in Upstream (`M`, 42 Files)

| # | File | Upstream Commit(s) | Impact / Conflict Risk | Strategy | Notes |
|---|---|---|---|---|---|
| 1 | `.gitignore` | `987f47d`, `9dd02cf` | Low | MERGE | Merge ignore rules (e.g. `skills/`, temp files) |
| 2 | `actions/attention_monitor.py` | `987f47d` | Low | REVIEW | Check compatibility |
| 3 | `actions/auto_heal_engine.py` | `987f47d` | Low | REVIEW | Verify DPAPI key access is preserved |
| 4 | `actions/brahma_connect.py` | `987f47d`, `9dd02cf` | Medium | REVIEW | Gateway connection helpers |
| 5 | `actions/instagram_mcp.py` | `987f47d` | Low | REVIEW | Check tool spec compatibility |
| 6 | `actions/open_app.py` | `9dd02cf` | Low | REVIEW | Minor parameter fixes |
| 7 | `actions/proactive.py` | `987f47d` | Medium | REVIEW | Background proactive tasks |
| 8 | `actions/screen_processor.py` | `9dd02cf` | Low | REVIEW | Minor fix |
| 9 | `actions/spotify_controller.py` | `987f47d` | Low | REVIEW | Check compatibility |
| 10 | `assets/globe/index.html` | `987f47d` | Low | REVIEW | Visual enhancements |
| 11 | `assets/web_background/index.html` | `987f47d` | High | MERGE | Contrast and shader improvements |
| 12 | `brahma-connect-android/.../index.html` | `987f47d` | Low | MERGE | Android web assets |
| 13 | `brahma_connect/gateway/command_router.py` | `9dd02cf` | Medium | REVIEW | Maintain route auth |
| 14 | `brahma_connect/gateway/device_manager.py` | `9dd02cf` | Medium | REVIEW | Maintain pairing enforcement |
| 15 | `brahma_connect/gateway/server.py` | `987f47d`, `9dd02cf` | CRITICAL | REJECT CHANGES | Upstream disabled pairing; our WS-8 auth must stand |
| 16 | `brahma_connect/gateway/websocket.py` | `987f47d` | Low | REVIEW | Reconnect handling |
| 17 | `build_all.ps1` | `b3c4160` | Medium | ADAPT | Integrate `Uninstall.exe` bundling into 3.11 build |
| 18 | `config/app_settings.json` | `987f47d` | Medium | MERGE | Merge new keys; keep DPAPI / trusted roots |
| 19 | `config/create_desktop_shortcut.ps1` | `987f47d` | Low | REVIEW | Shortcut creation helper |
| 20 | `core/circuit_hud.py` | `987f47d` | Low | REVIEW | Visual HUD update |
| 21 | `core/clipboard_sentry.py` | `987f47d` | Low | REVIEW | Clipboard monitoring |
| 22 | `core/dynamic_registry.py` | `987f47d` | High | EVALUATE | Stem matching vs Gemini Live 128-tool limit |
| 23 | `core/echo.py` | `9dd02cf` | High | ADAPT | Port acoustic barge-in gating |
| 24 | `core/globe_window.py` | `987f47d` | Low | REVIEW | Globe window widget |
| 25 | `core/learned_rules.py` | `987f47d` | Medium | ADAPT | Cherry-pick rule learning function |
| 26 | `core/prompt.txt` | `987f47d` | High | MERGE | Semantic merge for REA / image gen |
| 27 | `core/sensorium.py` | `987f47d` | Low | REVIEW | Sensorium updates |
| 28 | `dashboard/server.py` | `987f47d`, `9dd02cf` | Medium | REVIEW | Dashboard routes for voice/status |
| 29 | `dashboard/static/app.html` | `987f47d`, `9dd02cf` | Medium | REVIEW | Web UI updates |
| 30 | `features/__init__.py` | `987f47d` | Low | REVIEW | Verify safe loading under allowlist |
| 31 | `features/check_current_stock_prices.py`| `987f47d` | Low | REVIEW | Stock feature update |
| 32 | `gesture_utils.py` | `987f47d` | Low | REVIEW | Minor gesture tweak |
| 33 | `installer/BrahmaEvo.spec` | `c5e8162`, `b3c4160` | Medium | ADAPT | Spec file updates for uninstaller |
| 34 | `installer/BrahmaEvo_Setup.spec` | `c5e8162` | Medium | ADAPT | Installer packaging spec |
| 35 | `installer/install_wizard.py` | `987f47d` | Medium | REVIEW | Wizard enhancements |
| 36 | `llm_client.py` | `987f47d`, `62b1591` | High | ADAPT | Surgical patch for OpenRouter error handling |
| 37 | `main.py` | `987f47d`, `9dd02cf`, `62b1591` | CRITICAL | SURGICAL ONLY | Do NOT merge wholesale. `main.py` line ceiling $\le 3,230$ |
| 38 | `or_client.py` | `62b1591` | High | ADAPT | Adopt model pool fallback with DPAPI keys |
| 39 | `tests/test_autonomous_evolution.py` | `987f47d` | CRITICAL | REJECT | We deleted this legacy file in B4 |
| 40 | `tests/test_background_orb_startup.py` | `987f47d` | Low | REVIEW | Orb test check |
| 41 | `tests/test_daily_briefing.py` | `987f47d` | Low | REVIEW | Test tweak |
| 42 | `ui.py` | `987f47d`, `9dd02cf` | CRITICAL | ADAPT TO SEAMS | Do NOT merge into `ui.py`. Adapt to `core/ui_*` |

### 13.3 Deleted Files in Upstream (`D`, 1 File)

| # | File | Upstream Commit | Strategy | Notes |
|---|---|---|---|---|
| 1 | `config/.desktop_shortcut_created` | `987f47d` | ACCEPT DELETE | State marker file deletion |

---

## 14. Decision Log

- **DEC-UP-001 (2026-11-02): Full Scope Expansion to 7 Commits.**  
  Updated integration plan from 3 commits (81 files) to all 7 commits (114 files, `e0bd3c5..bfb2868`).
- **DEC-UP-002 (2026-11-02): Explicit Rejection of `core/subagent_orchestrator.py`.**  
  `9dd02cf` introduced an unconfined subagent orchestrator. Rejected in favor of our ratified `run_subagent` and `run_subagent_batch` under `AgentRuntime.execute`.
- **DEC-UP-003 (2026-11-02): Line-Count Ratchet Enforcement on Upstream Ports.**  
  Upstream's massive additions to `main.py` (+2,300 lines) and `ui.py` (+1,700 lines) must never be merged into those files. All new tool declarations must be added as `ToolSpec`s in `core/desktop_tools.py`, and UI additions must go into decomposed `core/ui_*` modules.
- **DEC-UP-004 (2026-11-02): OpenRouter Fallback Adaptation with DPAPI.**  
  Upstream's dynamic key reloading in `62b1591` is adapted to load keys through `core/secret_store.py:load_api_keys()`, maintaining Windows DPAPI encryption at rest.
