# Brahma-Ai-Evo: Upstream Integration Plan 

**Target:** Integrate upstream changes from [titechprabhasolutions/Brahma-Ai-Evo:main](https://github.com/SynapticSmith/Brahma-Ai-Evo/compare/main...titechprabhasolutions%3ABrahma-Ai-Evo%3Amain) (3 commits, +29857/-3186, 81 files) into our heavily refactored repository while preserving our 1.0.1 architecture. 

**Status:** Planning Only - No code changes yet  
**Created:** 2025-10-24  
**Base Commit:** `e0bd3c5a3a258cc1cf35014ae0a35c7b6b9db87f` (merge-base)  
**Our HEAD:** `3532e3b Add in-app avatar and background library`  
**Upstream HEAD:** `c8d9c0f feat(installer): add universal macOS/Linux installer scripts and GitHub Actions multi-platform build workflow`  

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Analysis](#current-state-analysis)
3. [Integration Strategy](#integration-strategy)
4. [Pre-Integration Checklist](#pre-integration-checklist)
5. [Phase 0: Preparation & Branch Setup](#phase-0-preparation--branch-setup)
6. [Phase 1: Low-Conflict Additions (Copy First)](#phase-1-low-conflict-additions-copy-first)
7. [Phase 2: Feature Ports Requiring Adaptation](#phase-2-feature-ports-requiring-adaptation)
8. [Phase 3: Surgical Fixes To Shared Files](#phase-3-surgical-fixes-to-shared-files)
9. [Phase 4: Validation & Testing](#phase-4-validation--testing)
10. [Rollback Plan](#rollback-plan)
11. [File Tracking Matrix](#file-tracking-matrix)
12. [Decision Log](#decision-log)
13. [Notes & Warnings](#notes--warnings)

---

## 1. Executive Summary

### The Verdict
**Port to our architecture, do NOT do a blind merge or insert upstream wholesale.** 

Our repository has undergone massive structural refactoring (273 files changed, +159k/-18k since merge-base) decomposing `ui.py` and `main.py` into 47+ `core/ui_*` modules, introducing `core/llm_gateway.py`, `core/provider_router.py`, `core/provider_failover.py`, `core/tool_index.py`, `core/tool_bridge.py`, `core/tool_exec.py`, and a comprehensive provider-agnostic architecture. Upstream's 3 commits were built against the monolithic pre-refactor baseline. A direct merge will cause extensive semantic conflicts and risk undoing our safety rails (provider kill switches, tool caps under Gemini Live limit, gateway enforcement).

### Why This Approach
- **Preserve Architecture:** Our decomposition prevents god objects and enforces proper separation of concerns
- **Maintain Safety Rails:** Provider toggles, kill switches, and gateway enforcement must remain intact
- **Extract Value:** Upstream adds valuable features (REA/web cloning, image generation, autonomous evolution) that complement our system
- **Minimize Risk:** Selective porting with adaptation prevents breaking existing functionality

---

## 2. Current State Analysis

### Repository State
```bash
Our Branch: main (HEAD: 3532e3b)
Remotes:
  origin: https://github.com/SynapticSmith/Brahma-Ai-Evo.git
  upstream: https://github.com/titechprabhasolutions/Brahma-Ai-Evo.git

Merge-base: e0bd3c5a3a258cc1cf35014ae0a35c7b6b9db87f
```

### Diff Statistics
| Source | Files Changed | Insertions | Deletions |
|--------|---------------|------------|-----------|
| Upstream (e0bd3c5..upstream/main) | 81 | +29,857 | -3,186 |
| Ours (e0bd3c5..HEAD) | 273 | +159,432 | -18,020 |
| Overlapping files | ~28 | - | - |

### Upstream Commits
1. **987f47d** (Oct 18, 2026) - "feat: Brahma Evo major release - neural image generation, REA web cloning, autonomous evolution, and UI contrast fixes" (81 files)
2. **c5e8162** (Oct 18, 2026) - "build(installer): bundle native Uninstall.exe and uninstall.cmd into BrahmaEvo_Setup installer package" (3 files)
3. **c8d9c0f** (Oct 18, 2026) - "feat(installer): add universal macOS/Linux installer scripts and GitHub Actions multi-platform build workflow" (3 files)

### Known Conflicts (from merge-tree)
Files that will have merge conflicts: `.gitignore`, `assets/web_background/index.html`, `config/app_settings.json`, `config/create_desktop_shortcut.ps1`, `core/globe_window.py`, `core/prompt.txt`, `features/__init__.py`, `installer/install_wizard.py`, `llm_client.py`, `main.py`, `ui.py`

---

## 3. Integration Strategy

### Strategy: Hybrid Selective Port
We will use a **feature-by-feature porting approach** with adaptation to our architecture, rather than merging. This preserves our refactored structure while extracting valuable upstream features.

### Decision Matrix
| Approach | Verdict | Rationale |
|----------|---------|-----------|
| `git merge upstream/main` | **REJECT** | Our UI/main decomposition incompatible with monolith assumptions. High conflict volume, semantic conflicts likely. Will undo gateway enforcement. |
| `git cherry-pick` individual commits | **PARTIAL** | Commits 2 & 3 (installer/CI) may be clean. Commit 1 is kitchen-sink mega-commit, cherry-pick will still hit conflicts in shared files. |
| **Selective port into our architecture** | **ACCEPT (RECOMMENDED)** | Copy new files as-is where possible, adapt shared file logic to our seams, route through gateway/router/index. Preserves safety rails. |

---

## 4. Pre-Integration Checklist

Before starting, ensure the following:

- [ ] **Clean working tree**: `git status` shows no uncommitted changes
- [ ] **Upstream fetched**: `git fetch upstream --prune` completed
- [ ] **Tests pass**: Run existing tests to establish baseline (`pytest` or relevant test command)
- [ ] **Backup created**: Tag current state `git tag backup/pre-upstream-integration-$(date +%Y%m%d)`
- [ ] **Documentation reviewed**: Read `docs/VERSION-1.0.1-LOG.md` for full context of our changes
- [ ] **Understand our architecture**: Familiarize with `core/llm_gateway.py`, `core/provider_router.py`, `core/tool_index.py`, `core/tool_bridge.py`

---

## 5. Phase 0: Preparation & Branch Setup

### Objectives
Create isolated integration branch and establish tracking mechanisms.

### Steps

#### 0.1 Create Integration Branch
```bash
cd C:\Users\Admin\Brahma-Ai-Evo
git fetch upstream --prune
git checkout -b integration/upstream-selective
git push -u origin integration/upstream-selective
```

#### 0.2 Create Integration Tracking Document
Already created at `docs/UPSTREAM-INTEGRATION-PLAN.md` (this document). Also create a decision log section to fill in during process.

#### 0.3 Generate File Inventory
```bash
# Generate detailed diff report
git diff --name-status e0bd3c5 upstream/main > docs/upstream-file-inventory.txt
git diff --stat e0bd3c5 upstream/main > docs/upstream-diff-stat.txt
```

#### 0.4 Establish Baseline Tests
```bash
# Run key tests to ensure clean baseline
pytest tests/test_ai_evolution.py tests/test_autonomous_evolution.py -v 2>&1 | tail -30
```

**Phase 0 Exit Criteria:**
- [ ] Integration branch created and pushed
- [ ] File inventories generated
- [ ] Baseline tests documented
- [ ] Backup tag created

---

## 6. Phase 1: Low-Conflict Additions (Copy First)

**Priority:** HIGH | **Risk:** LOW | **Goal:** Add new features that are mostly self-contained

### 1.1 Installer & CI (Commits c5e8162, c8d9c0f)
These are the most isolated changes. Consider cherry-picking first, else manual copy.

**Files:**
- `.github/workflows/build-all-platforms.yml` (NEW)
- `installer/install.sh` (NEW)
- `installer/uninstall.sh` (NEW)
- `installer/uninstall.cmd` (NEW)
- `installer/Uninstall.spec` (NEW)
- `installer/uninstall_wizard.py` (NEW)
- `installer/BrahmaEvo.spec` (MODIFIED)
- `installer/BrahmaEvo_Setup.spec` (MODIFIED)

**Actions:**
1. [ ] Try cherry-pick: `git cherry-pick c8d9c0f c5e8162` - see if clean
2. [ ] If conflicts in spec files, manually merge comparing with our existing specs
3. [ ] Reconcile `.github/workflows/build-all-platforms.yml` with our `.github/workflows/ci.yml` - don't duplicate, merge useful jobs
4. [ ] Test installer scripts syntax where possible

**Notes:** Our CI is already in place (`.github/workflows/ci.yml`). Upstream adds multi-platform build workflow - evaluate if both needed or consolidate.

### 1.2 Assets (HUDs, Backgrounds)
**Files:**
- `assets/radar/index.html` (NEW)
- `assets/space_cleaner/index.html` (NEW)
- `assets/globe/index.html` (MODIFIED)
- `assets/web_background/index.html` (MODIFIED) - **CONFLICT EXPECTED**
- `brahma-connect-android/app/src/main/assets/web_background/index.html` (MODIFIED)

**Actions:**
1. [ ] Copy new assets (`radar/`, `space_cleaner/`) wholesale
2. [ ] For modified assets, diff our version vs upstream and merge UI improvements carefully
3. [ ] Check if assets are referenced by our codebase or upstream codebase

### 1.3 New Features (features/ directory)
**Files:**
- `features/ascii_banner.py` (NEW)
- `features/ascii_banner/manifest.json` (NEW)
- `features/ascii_banner/skill.py` (NEW)
- `features/ascii_banner/test_cases.json` (NEW)
- `features/game_sentinel.py` (NEW)
- `features/net_sentinel.py` (NEW)
- `features/shopping_agent.py` (NEW)
- `features/study_guard.py` (NEW)
- `features/flowstate.py` (NEW)
- `features/check_current_stock_prices.py` (MODIFIED)
- `features/__init__.py` (MODIFIED) - **CONFLICT EXPECTED**

**Actions:**
1. [ ] Copy all new feature files
2. [ ] Merge `features/__init__.py` to register new features without breaking existing registrations
3. [ ] Review each feature's dependencies against our codebase

### 1.4 New Actions (actions/ directory)
**Files:**
- `actions/game_sentinel.py` (NEW)
- `actions/image_generator.py` (NEW)
- `actions/net_sentinel.py` (NEW)
- `actions/rea_mcp_client.py` (NEW)
- `actions/reverse_engineer.py` (NEW)
- `actions/study_guard.py` (NEW)
- `actions/attention_monitor.py` (MODIFIED)
- `actions/auto_heal_engine.py` (MODIFIED)
- `actions/brahma_connect.py` (MODIFIED)
- `actions/instagram_mcp.py` (MODIFIED)
- `actions/proactive.py` (MODIFIED)
- `actions/spotify_controller.py` (MODIFIED)

**Actions:**
1. [ ] Copy new action files
2. [ ] For modified actions, review changes - some may be useful fixes, but ensure compatibility with our refactored structure
3. [ ] Do NOT wire into main.py yet (Phase 2/3)

### 1.5 New Core Modules (self-contained)
**Files:**
- `core/anticipatory_watcher.py` (NEW)
- `core/execution_reflection.py` (NEW)
- `core/flowstate.py` (NEW)
- `core/game_sentinel.py` (NEW)
- `core/intent_dispatcher.py` (NEW)
- `core/mem_reduct.py` (NEW)
- `core/memory_cortex.py` (NEW)
- `core/net_sentinel.py` (NEW)
- `core/omni_planner.py` (NEW)
- `core/radar_hud.py` (NEW)
- `core/shopping_agent.py` (NEW)
- `core/space_cleaner.py` (NEW)
- `core/space_cleaner_hud.py` (NEW)
- `core/study_guard.py` (NEW)
- `core/clipboard_sentry.py` (MODIFIED)
- `core/learned_rules.py` (MODIFIED) - Can cherry-pick function
- `core/sensorium.py` (MODIFIED)

**Actions:**
1. [ ] Copy all new core modules
2. [ ] For `core/learned_rules.py`, extract `detect_and_learn_conversational_rule()` method if not present and useful
3. [ ] Hold off on wiring until Phase 2

### 1.6 New Tests
**Files:**
- `tests/test_ai_evolution.py` (NEW)
- `tests/test_flowstate.py` (NEW)
- `tests/test_game_sentinel.py` (NEW)
- `tests/test_image_generator.py` (NEW)
- `tests/test_intent_dispatcher.py` (NEW)
- `tests/test_mobile_connect_routing.py` (NEW)
- `tests/test_net_sentinel.py` (NEW)
- `tests/test_proactive_engine.py` (NEW)
- `tests/test_reverse_engineer.py` (NEW)
- `tests/test_shopping_agent.py` (NEW)
- `tests/test_space_cleaner.py` (NEW)
- `tests/test_study_guard.py` (NEW)
- `tests/test_ui_upgrades.py` (NEW)
- Various test modifications

**Actions:**
1. [ ] Copy new tests
2. [ ] Run them after wiring features to check compatibility
3. [ ] Adapt imports/paths if needed for our structure

**Phase 1 Exit Criteria:**
- [ ] All new files copied (no conflicts on new files)
- [ ] Installer/CI files in place
- [ ] Assets copied
- [ ] Features/actions/core modules copied
- [ ] Tests copied
- [ ] Git status clean of unmerged conflicts from Phase 1

---

## 7. Phase 2: Feature Ports Requiring Adaptation

**Priority:** HIGH | **Risk:** MEDIUM-HIGH | **Goal:** Integrate valuable features through our architecture

### 2.1 Image Generation (`actions/image_generator.py`)
**Complexity:** Medium | **Value:** High

**Current State:** Copied in Phase 1 but not wired  
**Our Architecture:** Must go through `core/llm_gateway.py`, respect provider toggles/failover

**Adaptation Steps:**
1. [ ] Review `actions/image_generator.py` - understand its API, providers, dependencies
2. [ ] Check if it calls LLM providers directly or has its own routing
3. [ ] Refactor to use `core/llm_gateway.py` for any text/LLM operations
4. [ ] Integrate with `core/provider_router.py` and respect kill switches
5. [ ] Add settings to `config/app_settings.json` (merge keys, don't overwrite)
6. [ ] Wire into tool system via `core/tool_index.py` / `core/tool_bridge.py`
7. [ ] Add to features if needed
8. [ ] Test with `tests/test_image_generator.py` (adapt if necessary)

**Key Files to Modify:** `actions/image_generator.py` (adapt), tool registration files

### 2.2 REA Web Cloning & Reverse Engineering
**Files:** `actions/rea_mcp_client.py`, `actions/reverse_engineer.py`  
**Complexity:** Medium | **Value:** High

**Adaptation Steps:**
1. [ ] Review both files for architecture assumptions
2. [ ] Integrate with our tool execution system (`core/tool_exec.py`, `core/tool_bridge.py`)
3. [ ] Check interaction with `core/intent_dispatcher.py` (upstream adds it) - we may have our own routing
4. [ ] Decide: use upstream intent_dispatcher as-is, adapt it, or integrate differently
5. [ ] Ensure tools are registered in `core/tool_index.py` with proper descriptions
6. [ ] Route any network/LLM calls through gateway if applicable
7. [ ] Test with `tests/test_reverse_engineer.py`

**Considerations:** Upstream's `core/intent_dispatcher.py` may conflict with our routing logic. Need to audit our existing routing first.

### 2.3 Autonomous Evolution Suite
**Files:** `core/flowstate.py`, `core/memory_cortex.py`, `core/omni_planner.py`, `core/anticipatory_watcher.py`, `core/execution_reflection.py`, `core/mem_reduct.py`  
**Complexity:** HIGH | **Value:** Potentially Very High  
**Risk:** May duplicate or conflict with existing systems

**Adaptation Steps:**
1. [ ] **Audit First:** Review our existing memory/learning/planning systems
   - Check for memory-related code in our codebase
   - Understand our current architecture patterns
   - Look at `docs/VERSION-1.0.1-LOG.md` for memory-related changes
2. [ ] **Gap Analysis:** Compare upstream modules against our capabilities
3. [ ] **Selective Integration:** Only port pieces that fill genuine gaps
   - `execution_reflection.py` - interesting for self-improvement
   - `anticipatory_watcher.py` - proactive behavior
   - Others may overlap with existing systems
4. [ ] **Avoid Duplication:** Don't replace working systems, extend them
5. [ ] **Test Thoroughly:** Run `tests/test_ai_evolution.py`, `tests/test_autonomous_evolution.py`, `tests/test_proactive_engine.py`, `tests/test_flowstate.py`
6. [ ] **Wire Carefully:** May need integration points in `main.py` (Phase 3)

**Warning:** This is the highest risk area. Take time to understand before integrating.

### 2.4 Sentinel/HUD Features
**Files:** `actions/game_sentinel.py`, `actions/net_sentinel.py`, `actions/study_guard.py`, `actions/shopping_agent.py`, corresponding `core/*`, `features/*`, assets, HUDs  
**Complexity:** Low-Medium | **Value:** Nice-to-have

**Adaptation Steps:**
1. [ ] Review each sentinel for dependencies
2. [ ] Check if they integrate with our UI system (`core/ui_*` modules) or expect monolith `ui.py`
3. [ ] If UI integration needed, adapt to our decomposed UI architecture (add to appropriate `core/ui_*.py` modules)
4. [ ] Copy assets if not already copied
5. [ ] Register features/actions as appropriate
6. [ ] Test with corresponding tests

**Phase 2 Exit Criteria:**
- [ ] Image generation ported and routing through gateway
- [ ] REA/reverse engineer integrated with tool system
- [ ] Evolution suite audited, gaps filled selectively
- [ ] Sentinels reviewed and integrated where compatible
- [ ] No direct LLM calls bypassing gateway
- [ ] All ported features use our provider toggles

---

## 8. Phase 3: Surgical Fixes To Shared Files

**Priority:** CRITICAL | **Risk:** VERY HIGH | **Goal:** Merge upstream improvements without breaking our architecture

**Handle these LAST.** These are the most conflicted files and require deep understanding of both versions.

### 3.1 `core/prompt.txt` - **CONFLICT**
**Our version:** Rewritten as multi-step agent brief with full capability map, deferred-tool discovery, memory/safety/offline doctrine (see VERSION-1.0.1-LOG section 16)  
**Upstream adds:** RE/reverse engineering instructions, image generation instructions

**Strategy:** SEMANTIC MERGE - Keep our structure, add upstream's feature instructions

**Actions:**
1. [ ] Read both versions completely
2. [ ] Keep our rewritten brief structure (multi-step turn loop, capability inventory, etc.)
3. [ ] Add upstream's "REVERSE ENGINEERING & APPLICATION CLONING" section
4. [ ] Add upstream's "IMAGE GENERATION & VISUAL ART" section
5. [ ] Ensure consistency with our existing tone/style
6. [ ] Don't lose our local-model context budget notes or other additions

### 3.2 `core/learned_rules.py` - **MODIFIED**
**Upstream adds:** `detect_and_learn_conversational_rule()` method  
**Our version:** May have different structure

**Strategy:** CHERRY-PICK FUNCTION

**Actions:**
1. [ ] Compare both versions
2. [ ] Add `detect_and_learn_conversational_rule()` if not present
3. [ ] Test to ensure no conflicts
4. [ ] Consider wiring it into conversation flow if useful

### 3.3 `core/dynamic_registry.py` - **MODIFIED**
**Upstream changes:** Stem expansion, threshold raised to 70, added required parameter validation

**Strategy:** CAREFUL EVALUATION

**Our concern:** We tuned for Gemini Live cap (128 tools advertised, ~24 sent). Threshold changes affect skill matching.

**Actions:**
1. [ ] Review upstream changes against our needs
2. [ ] Test skill matching behavior
3. [ ] If upstream improvements don't hurt our cap management, consider adopting
4. [ ] Keep our optimizations intact

### 3.4 `llm_client.py` - **HEAVY CONFLICT**
**Our version:** Integrates with `provider_router`, `llm_gateway`  
**Upstream version:** Different architecture

**Strategy:** EXTRACT FIXES ONLY - Do NOT replace

**Actions:**
1. [ ] Never wholesale replace our `llm_client.py`
2. [ ] Diff upstream vs our version line by line
3. [ ] Extract only specific bug fixes/timeouts/routing fixes
4. [ ] Apply as surgical patches
5. [ ] Ensure all changes go through our gateway/router
6. [ ] Test provider failover thoroughly

### 3.5 `main.py` - **MASSIVE CONFLICT**
**Our version:** Decomposed boot flow via `core/ui_shell_boot`, etc.  
**Upstream adds:** Wiring for new features (image gen, REA, sentinels, evolution suite)

**Strategy:** WIRE NEW FEATURES, DON'T MERGE MONOLITH

**Actions:**
1. [ ] Do NOT replace our main.py
2. [ ] Identify new entry points/callbacks upstream adds for ported features
3. [ ] Wire them into our decomposed boot flow
4. [ ] Check where upstream initializes new modules and replicate in our structure
5. [ ] May need additions to `core/ui_shell_boot.py` or other boot modules
6. [ ] Test boot sequence thoroughly

### 3.6 `ui.py` - **CONFLICT (MAJOR)**
**Our version:** Facade over 47 `core/ui_*` modules  
**Upstream adds:** UI features, HUDs, contrast fixes

**Strategy:** PORT TO `core/ui_*` MODULES, KEEP FACADE

**Actions:**
1. [ ] Do NOT replace our facade `ui.py`
2. [ ] Identify UI changes in upstream
3. [ ] Port to appropriate `core/ui_*.py` modules:
   - HUDs -> relevant UI modules
   - Overlays -> `core/ui_overlays*.py`
   - Settings -> `core/ui_shell_settings.py` or `core/ui_appsettings.py`
   - etc.
4. [ ] Keep `ui.py` minimal as facade
5. [ ] Test UI rendering

### 3.7 `config/app_settings.json` - **CONFLICT**
**Strategy:** MERGE KEYS, PRESERVE STRUCTURE

**Actions:**
1. [ ] Never overwrite wholesale
2. [ ] Add new keys from upstream that are relevant
3. [ ] Keep our existing settings intact
4. [ ] Check schema compatibility
5. [ ] Update defaults carefully

### 3.8 Other Shared Files
| File | Strategy | Notes |
|------|----------|-------|
| `.gitignore` | Merge both | Add upstream ignores if useful, keep ours |
| `assets/web_background/index.html` | Manual merge | Large file, diff carefully |
| `config/create_desktop_shortcut.ps1` | Merge | Compare functionality |
| `core/globe_window.py` | Merge | Check for conflicts in structure |
| `core/circuit_hud.py` | Review | Upstream modified, check compatibility |
| `core/dynamic_registry.py` | Selective | See 3.3 |
| `core/sensorium.py` | Review | Modified by both |
| `dashboard/server.py` | Review | May need updates for new features |
| `dashboard/static/app.html` | Review | UI changes |
| `features/__init__.py` | Merge registrations | Add new features without breaking existing |
| `gesture_utils.py` | Review | Minor changes |
| `installer/install_wizard.py` | Merge carefully | Compare with our flow |
| `tests/test_autonomous_evolution.py` | Review | Both modified |
| `tests/test_daily_briefing.py` | Review | Minor |
| `brahma_connect/gateway/server.py` | Review | Mobile connectivity |
| `brahma_connect/gateway/websocket.py` | Review | Mobile connectivity |

**Phase 3 Exit Criteria:**
- [ ] All conflicts resolved
- [ ] No files reverted to monolith structure
- [ ] Gateway/router still used for all LLM egress
- [ ] Prompt.txt merged semantically
- [ ] main.py/ui.py remain in decomposed/facade form
- [ ] Git status clean (no merge conflicts)

---

## 9. Phase 4: Validation & Testing

**Priority:** CRITICAL | **Risk:** HIGH

### 9.1 Static Checks
```bash
# Syntax check Python files
python -m py_compile main.py ui.py llm_client.py
# Check for obvious import issues
```

### 9.2 Run Existing Tests (Regression)
```bash
# Provider/gateway tests
pytest tests/ -k "gateway or provider or router or failover" -v

# Core functionality
pytest tests/test_ai_evolution.py tests/test_autonomous_evolution.py -v

# UI tests
pytest tests/test_background_orb_startup.py tests/test_daily_briefing.py -v
```

### 9.3 Run New Upstream Tests
```bash
# Features we ported
pytest tests/test_image_generator.py -v
pytest tests/test_reverse_engineer.py -v
pytest tests/test_flowstate.py tests/test_proactive_engine.py -v
pytest tests/test_game_sentinel.py tests/test_net_sentinel.py tests/test_shopping_agent.py tests/test_study_guard.py -v
pytest tests/test_intent_dispatcher.py -v
pytest tests/test_mobile_connect_routing.py -v
pytest tests/test_space_cleaner.py -v
pytest tests/test_ui_upgrades.py -v
```

### 9.4 Manual Smoke Testing
- [ ] **Boot**: Application starts without errors
- [ ] **Provider Routing**: Test with different providers, verify kill switches work
- [ ] **Gateway**: Confirm all LLM calls go through `core/llm_gateway.py`
- [ ] **Tool System**: Tool index/cap still enforced (under Gemini Live limit)
- [ ] **Image Generation**: Test if ported, routes through gateway
- [ ] **REA/Reverse Engineer**: Test if ported
- [ ] **UI**: All UI modules load, no broken imports
- [ ] **Mobile Connect**: Test if affected

### 9.5 Architecture Validation
- [ ] No new god objects created (check file sizes of main.py, ui.py)
- [ ] Provider toggles enforced everywhere
- [ ] Tool count under control
- [ ] Gateway is single egress point
- [ ] Decomposed structure preserved

### 9.6 Documentation Update
- [ ] Update `docs/VERSION-1.0.1-LOG.md` with upstream integration section, or create `docs/VERSION-1.0.2-LOG.md`
- [ ] Update `docs/UPSTREAM-INTEGRATION-PLAN.md` Decision Log with all decisions made
- [ ] Document any deviations from plan

**Phase 4 Exit Criteria:**
- [ ] All regression tests pass
- [ ] New tests pass (or adapted and passing)
- [ ] Smoke tests successful
- [ ] Architecture intact
- [ ] Documentation updated

---

## 10. Rollback Plan

If integration goes wrong at any point:

### Immediate Rollback
```bash
# Abort if in merge/conflict state
git merge --abort  # if merging
git cherry-pick --abort  # if cherry-picking
git reset --hard HEAD  # if needed
```

### Full Rollback to Pre-Integration
```bash
git checkout main
git reset --hard backup/pre-upstream-integration-YYYYMMDD
# Or to specific tag
git reset --hard <backup-tag>
```

### Partial Rollback
Use `git revert` for specific commits, or `git checkout <file>` from backup.

### Safety Measures
- Work on separate integration branch (never directly on main until validated)
- Create backup tag before starting
- Commit frequently with clear messages
- Test after each phase

---

## 11. File Tracking Matrix

Track every upstream file's status. Update as you go.

### New Files (A)
| File | Phase | Status | Decision | Notes |
|------|-------|--------|----------|-------|
| `.github/workflows/build-all-platforms.yml` | 1.1 | PENDING | - | Evaluate vs our ci.yml |
| `actions/game_sentinel.py` | 1.4 | PENDING | - | Port with adaptation |
| `actions/image_generator.py` | 1.4/2.1 | PENDING | - | Must route through gateway |
| `actions/net_sentinel.py` | 1.4 | PENDING | - | - |
| `actions/rea_mcp_client.py` | 1.4/2.2 | PENDING | - | Port to tool system |
| `actions/reverse_engineer.py` | 1.4/2.2 | PENDING | - | Port to tool system |
| `actions/study_guard.py` | 1.4 | PENDING | - | - |
| `assets/radar/index.html` | 1.2 | PENDING | - | Copy |
| `assets/space_cleaner/index.html` | 1.2 | PENDING | - | Copy |
| `brahma_connect/gateway/mobile_portal.py` | 1.6 context | PENDING | - | Review |
| `core/anticipatory_watcher.py` | 1.5/2.3 | PENDING | - | Evaluate for gaps |
| `core/execution_reflection.py` | 1.5/2.3 | PENDING | - | High value candidate |
| `core/flowstate.py` | 1.5/2.3 | PENDING | - | - |
| `core/game_sentinel.py` | 1.5 | PENDING | - | - |
| `core/intent_dispatcher.py` | 1.5/2.2 | PENDING | - | May conflict with routing |
| `core/mem_reduct.py` | 1.5/2.3 | PENDING | - | Evaluate |
| `core/memory_cortex.py` | 1.5/2.3 | PENDING | - | Evaluate vs existing |
| `core/net_sentinel.py` | 1.5 | PENDING | - | - |
| `core/omni_planner.py` | 1.5/2.3 | PENDING | - | Evaluate |
| `core/radar_hud.py` | 1.5 | PENDING | - | UI integration needed |
| `core/shopping_agent.py` | 1.5 | PENDING | - | - |
| `core/space_cleaner.py` | 1.5 | PENDING | - | - |
| `core/space_cleaner_hud.py` | 1.5 | PENDING | - | - |
| `core/study_guard.py` | 1.5 | PENDING | - | - |
| `features/ascii_banner/*` | 1.3 | PENDING | - | Copy + register |
| `features/game_sentinel.py` | 1.3 | PENDING | - | Copy + register |
| `features/net_sentinel.py` | 1.3 | PENDING | - | Copy + register |
| `features/shopping_agent.py` | 1.3 | PENDING | - | Copy + register |
| `features/study_guard.py` | 1.3 | PENDING | - | Copy + register |
| `features/flowstate.py` | 1.3 | PENDING | - | Copy + register |
| `installer/Uninstall.spec` | 1.1 | PENDING | - | - |
| `installer/install.sh` | 1.1 | PENDING | - | - |
| `installer/uninstall.cmd` | 1.1 | PENDING | - | - |
| `installer/uninstall.sh` | 1.1 | PENDING | - | - |
| `installer/uninstall_wizard.py` | 1.1 | PENDING | - | Review vs our flow |
| `tests/test_ai_evolution.py` | 1.6 | PENDING | - | - |
| `tests/test_flowstate.py` | 1.6 | PENDING | - | - |
| `tests/test_game_sentinel.py` | 1.6 | PENDING | - | - |
| `tests/test_image_generator.py` | 1.6 | PENDING | - | Adapt to gateway |
| `tests/test_intent_dispatcher.py` | 1.6 | PENDING | - | - |
| `tests/test_mobile_connect_routing.py` | 1.6 | PENDING | - | - |
| `tests/test_net_sentinel.py` | 1.6 | PENDING | - | - |
| `tests/test_proactive_engine.py` | 1.6 | PENDING | - | - |
| `tests/test_reverse_engineer.py` | 1.6 | PENDING | - | Adapt |
| `tests/test_shopping_agent.py` | 1.6 | PENDING | - | - |
| `tests/test_space_cleaner.py` | 1.6 | PENDING | - | - |
| `tests/test_study_guard.py` | 1.6 | PENDING | - | - |
| `tests/test_ui_upgrades.py` | 1.6 | PENDING | - | - |

### Modified Files (M) - High Conflict Risk
| File | Phase | Status | Decision | Notes |
|------|-------|--------|----------|-------|
| `.gitignore` | 3.8 | PENDING | MERGE | - |
| `actions/attention_monitor.py` | 1.4 | PENDING | REVIEW | Check compatibility |
| `actions/auto_heal_engine.py` | 1.4 | PENDING | REVIEW | Check compatibility |
| `actions/brahma_connect.py` | 1.4 | PENDING | REVIEW | Check compatibility |
| `actions/instagram_mcp.py` | 1.4 | PENDING | REVIEW | Check compatibility |
| `actions/proactive.py` | 1.4 | PENDING | REVIEW | Check compatibility |
| `actions/spotify_controller.py` | 1.4 | PENDING | REVIEW | Check compatibility |
| `assets/globe/index.html` | 1.2 | PENDING | REVIEW | - |
| `assets/web_background/index.html` | 1.2 | PENDING | MERGE | Large conflict |
| `brahma-connect-android/app/src/main/assets/web_background/index.html` | 1.2 | PENDING | REVIEW | - |
| `brahma_connect/gateway/server.py` | 3.8 | PENDING | REVIEW | - |
| `brahma_connect/gateway/websocket.py` | 3.8 | PENDING | REVIEW | - |
| `config/app_settings.json` | 3.7 | PENDING | MERGE_KEYS | Never overwrite |
| `config/create_desktop_shortcut.ps1` | 3.8 | PENDING | MERGE | - |
| `core/circuit_hud.py` | 3.8 | PENDING | REVIEW | - |
| `core/clipboard_sentry.py` | 1.5 | PENDING | REVIEW | - |
| `core/dynamic_registry.py` | 3.3 | PENDING | EVALUATE | Watch cap/threshold |
| `core/globe_window.py` | 3.8 | PENDING | REVIEW | - |
| `core/learned_rules.py` | 1.5/3.2 | PENDING | CHERRY_PICK | Add function only |
| `core/prompt.txt` | 3.1 | PENDING | SEMANTIC_MERGE | Keep our structure |
| `core/sensorium.py` | 1.5/3.8 | PENDING | REVIEW | - |
| `dashboard/server.py` | 3.8 | PENDING | REVIEW | - |
| `dashboard/static/app.html` | 3.8 | PENDING | REVIEW | - |
| `features/__init__.py` | 1.3/3.8 | PENDING | MERGE | - |
| `features/check_current_stock_prices.py` | 1.3 | PENDING | REVIEW | - |
| `gesture_utils.py` | 3.8 | PENDING | REVIEW | - |
| `installer/BrahmaEvo.spec` | 1.1 | PENDING | MERGE | - |
| `installer/BrahmaEvo_Setup.spec` | 1.1 | PENDING | MERGE | - |
| `installer/install_wizard.py` | 3.8 | PENDING | MERGE | - |
| `llm_client.py` | 3.4 | PENDING | EXTRACT_FIXES | NEVER REPLACE |
| `main.py` | 3.5 | PENDING | WIRE_FEATURES | NEVER REPLACE |
| `tests/test_autonomous_evolution.py` | 3.8 | PENDING | REVIEW | Both modified |
| `tests/test_background_orb_startup.py` | 3.8 | PENDING | REVIEW | - |
| `tests/test_daily_briefing.py` | 3.8 | PENDING | REVIEW | - |
| `ui.py` | 3.6 | PENDING | PORT_TO_MODULES | NEVER REPLACE |
| `config/.desktop_shortcut_created` | - | PENDING | DELETE | Upstream deletes this |

### Deleted Files (D)
| File | Phase | Status | Decision | Notes |
|------|-------|--------|----------|-------|
| `config/.desktop_shortcut_created` | 1.1 | PENDING | DELETE | Matches upstream |

**Status Legend:** PENDING | IN_PROGRESS | DONE | SKIPPED | BLOCKED

---

## 12. Decision Log

Use this section to record all integration decisions during the process.

| Date | File/Feature | Decision | Rationale | Reviewer |
|------|---------------|----------|-----------|----------|
| 2025-10-24 | Overall Strategy | Selective port to our architecture | Preserve decomposition, gateway enforcement, safety rails | User/Agent |
| 2025-10-24 | Commits c5e8162, c8d9c0f | Try cherry-pick first | Isolated installer/CI changes | TBD |
| 2025-10-24 | Commit 987f47d | Do NOT cherry-pick wholesale | Kitchen-sink mega-commit against monolith | TBD |
| 2025-10-24 | main.py, ui.py, llm_client.py | Never replace wholesale | Core of our refactored architecture | TBD |
| TBD | core/intent_dispatcher.py vs our routing | TBD | Need to audit our routing first | TBD |
| TBD | Autonomous evolution suite | TBD | May duplicate existing systems | TBD |

---

## 13. Notes & Warnings

### Critical Warnings
- **NEVER replace main.py or ui.py wholesale** - These are facades over our decomposed architecture. Replacing will break everything.
- **NEVER bypass the gateway** - All LLM calls must go through `core/llm_gateway.py`. Image generation and any new features must comply.
- **Preserve provider toggles** - Our kill switches must remain functional. Don't let new code bypass them.
- **Watch tool count** - We maintain Gemini Live cap compliance. Don't bloat tool registry.
- **Test after each phase** - Don't batch everything, validate incrementally.

### Tips
- When in doubt, look at how our codebase does it in the refactored structure, not how upstream does it
- Use `git diff` extensively to understand changes
- Keep commits small and focused per feature/phase
- Update the tracking matrix in real-time
- Document any architectural adaptations you make

### Useful Commands
```bash
# Check merge conflicts
git diff --name-only --diff-filter=U

# See what upstream changed in specific file
git show upstream/main:path/to/file

# Compare our version vs upstream for specific file
git diff e0bd3c5 upstream/main -- path/to/file

# Test specific functionality
pytest tests/test_X.py -v

# Check imports
python -c "import main, ui, llm_client"
```

---

**END OF PLAN** 

*This document is a living plan. Update the tracking matrix, decision log, and status as you progress through the integration.* 
