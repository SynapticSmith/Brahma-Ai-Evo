# Knowledge & Skills Engine — Implementation Progress Tracker

> **Repository Tracker Document:** [`docs/KNOWLEDGE-AND-SKILLS-ENGINE-TRACKER.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/KNOWLEDGE-AND-SKILLS-ENGINE-TRACKER.md)  
> **Master Architecture Specification:** [`docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md) (Rev 3.1)  
> **Current Date:** 2026-11-11  

---

## High-Level Phase Overview

| Phase | Description | Status | Target Deliverables | Exit Criteria |
|:---|:---|:---:|:---|:---:|
| **Phase 0** | **Audit & Snapshot** (Read-Only) | 🟢 **COMPLETED** | `_audit/taxonomy-audit.md`, `taxonomy.json`, vault tag/branch `taxonomy/audit-v1` | [x] Passed |
| **Phase 1** | **Normalize Tree & Transplant Notes** | 🟢 **COMPLETED** | `taxonomy-migration.json`, `_audit/link-rewrite.json`, `tools/taxonomy.py` | [x] Passed |
| **Phase 2** | **Wikipedia Tree Seeding** | 🟢 **COMPLETED** | `tools/seed_academic_taxonomy.py`, cached JSON/MD in `cache/wikipedia/` | [x] Passed |
| **Phase 3** | **Retrieval Schema v2 & Type Ranking** | 🟢 **COMPLETED** | SQLite schema migration, type-weighted FTS5, `max 2 index pages` rule | [x] Passed |
| **Phase 4** | **Multimodal Conversion & OCR Pipeline** | 🟢 **COMPLETED** | `tools/ingest_multimodal.py`, `markitdown` + asset extractor | [x] Passed |
| **Phase 5** | **Knowledge Extraction & Placement** | 🟢 **COMPLETED** | `tools/extract_knowledge.py`, segmentation, atomization, `_inbox/` routing | [x] Passed |
| **Phase 6** | **JIT Skills & Scripts Engine** | 🟢 **COMPLETED** | `TurnPlan` compiler, JIT SOP injection, `config/scripts.json`, `turn_context.py` | [x] Passed |
| **Phase 7** | **Teacher Escalation & Worktree Harness** | 🟡 **PARTIAL** | `tools/worktree.py`, `agent/teacher_policy.py`, AST & sandbox safety gates (policy module + tests only; no production escalation caller) | [ ] Not wired |
| **Hardening** | **Gemini Live 1007 Disconnection Fix** | 🟢 **COMPLETED** | Stream pause during tool execution, resume handle invalidation on 1007/1008 | [x] Passed |
| **Track 8** | **Scripts Indexer & Safety Scanner** | 🟢 **COMPLETED** | `tools/scripts_index.py` (`--build`, `--check`, `--json`), AST audit | [x] Passed |
| **Track 9** | **Taxonomized Skills Index & Cookbooks** | 🟢 **COMPLETED** | `tools/skills_index.py`, OpenMonoAgent cookbooks for small models (`lfm3:1.7b`) | [x] Passed |
| **Track 10** | **Cognitive Thinking Skills Portfolio Ingestion** | 🟢 **COMPLETED** | Port 9 mental model skills from `cc-thinking-skills` into `skills/` + cookbooks | [x] Passed |
| **Harness Tool** | **`search_scripts` & HUD / CLI Integration** | 🟢 **COMPLETED** | `search_scripts` ToolSpec (§9.9, §16), CLI `scripts`/`cookbook`, HUD slash commands | [x] Passed |
| **Live Ingestion** | **Companion Books Batch Extraction & Placement** | 🟢 **COMPLETED** | 6 companion books scanned, 153 units extracted (102 placed, 51 inboxed), 854 chunks indexed | [x] Passed |
| **Track 11** | **Inbox Note Promotion Engine & Placement** | 🟢 **COMPLETED** | `tools/promote_inbox.py` (`--list`, `--promote`, `--auto-source`), 89 notes promoted in the initial batch (folder now holds 265) | [x] Passed |
| **Track 12** | **Portfolio Skills Absorption (Batch 1: 15 Skills)** | 🟢 **COMPLETED** | 15 skills from `superpowers`, `spec-kit`, `procedure-ops`, `cc-thinking-skills`, `SkillOpt` (39 total) | [x] Passed |
| **Track 13** | **Portfolio Skills Absorption (Batch 2: 16 Skills)** | 🟢 **COMPLETED** | 16 skills from `answer-me-with-html`, `anatomy`, `ffmpeg-skill`, `OfficeCLI`, `code-graph-rag` (55 total) | [x] Passed |
| **Track 14** | **OpenMonoAgent Playbooks Absorption (7 Skills)** | 🟢 **COMPLETED** | 7 production playbooks: `incident-response`, `db-migrate`, `release`, `pr-ready`, `secret-scan`, `commit`, `blast-radius` (62 total) | [x] Passed |
| **Track 15** | **Portfolio & OpenMono Absorption (Batch 4: 8 Skills)** | 🟢 **COMPLETED** | 8 skills: `server-deploy`, `llms-txt`, `windows-gui`, `web-research`, `scroll-craft`, `grounded-claims`, `mimic-ipc`, `pg-opt` (70 total) | [x] Passed |
| **Track 16** | **Portfolio Skills Absorption (Batch 5: 10 Skills)** | 🟢 **COMPLETED** | 10 skills: `text-to-cad`, `book-to-skill`, `vega-viz`, `canvas-designer`, `media-crawler`, `sqlite-fts`, `openapi-contract`, `circuit-breaker`, `canary-deploy`, `epistemic-arbitrage` (80 total) | [x] Passed |
| **Track 17** | **Portfolio Skills Absorption (Batch 6: 10 Skills) & UX Integration** | 🟢 **COMPLETED** | 10 skills: `smart-scraper`, `graph-rag`, `structured-grammar`, `answer-engine`, `doc-converter`, `flakiness-hunter`, `sunk-cost`, `loss-aversion`, `survivorship-bias`, `confirmation-bias` (90 total) + `/domains`, `/brain` HUD commands & CLI `--domain` filter | [x] Passed |
| **Track 18** | **External Software Installation & Bootstrap Automation** | 🟢 **COMPLETED** | Installed `rg`, `ffmpeg`, `yt-dlp`, `pandoc`, `sqlite3`, `openscad`; enhanced `setup.py`, `scripts/setup_dev.ps1`, `scripts/install_system_tools.ps1` | [x] Passed |
| **Verification Gate** | **Full Portable Test Suite & Ratchets** | 🟢 **COMPLETED** | 1,716 tests passed (7 skipped), 0 ruff errors, architecture ratchets clean | [x] Passed |

> **Audit note (2026-11-11):** A read-only pass over this tracker found the
> knowledge index and the skills/scripts engine live and working: the vault
> resolves, the FTS index covers the 3,646 searchable pages, `skills_index.py`
> validates 90 skills, `scripts_index.py` validates 21 scripts, and both
> `--check`s pass. One real defect was found — `compile_turn_context` let the
> rendered skill block exceed `skill_budget` — and is fixed in commit
> `f33beac`. Phase 7 is **partial**: `agent/teacher_policy.py` and
> `tools/worktree.py` exist with tests, but nothing in production calls the
> escalation policy, so no failure gap is detected and no teacher is invoked.
> The counts in this document were refreshed to match the tree; the buddhism
> folder holds 265 notes now, not the 89 promoted in the initial batch.

---

## Architecture Flowchart

```mermaid
flowchart TD
    subgraph KNOWLEDGE_CORE["Knowledge Layer (Second Brain)"]
        P0["Phase 0: Audit & Snapshot<br/>(2,520 stubs cataloged, 321 substantive notes preserved)"] --> P1["Phase 1: Tree Normalization<br/>(122 spine nodes, 0 broken links)"]
        P1 --> P2["Phase 2: Wikipedia Tree Seeding<br/>(122/122 lead sections + companion articles seeded)"]
        P2 --> P3["Phase 3: Schema v2 & Type-Weighted Ranking<br/>(Zero-downtime SQLite migration, max 2 index pages cap)"]
    end

    subgraph INGESTION_PIPELINE["Ingestion & Extraction Layer"]
        P4["Phase 4: Multimodal Ingestion Pipeline<br/>(MarkItDown, glyph density check, figure asset extraction)"] --> P5["Phase 5: Knowledge Extraction & Placement<br/>(BM25 taxonomy routing, inbox fallback, Calyx anchoring)"]
    end

    subgraph HARNESS_INTELLIGENCE["Offloaded Harness Intelligence"]
        P5 --> P6["Phase 6: JIT Skills & Scripts Engine<br/>(TurnPlan, SOP shortlisting & budget clamping, scripts catalogue)"]
        P6 --> P7["Phase 7: Teacher Escalation & Git Worktree Harness<br/>(Isolated worktrees, AST safety gates; teacher policy scaffold, not wired)"]
        P7 --> P8["Live WebSocket Hardening<br/>(Stream pause on tool exec, non-None ID guarantee, resume handle safety)"]
        P8 --> T8["Track 8: Scripts Indexer & Safety Scanner<br/>(AST signature scan, safety classification gate, --build/--check)"]
        T8 --> T9["Track 9: Taxonomized Skills Index & Cookbooks<br/>(OpenMonoAgent recipes, domain classification, lfm3:1.7b execution)"]
        T9 --> T10["Track 10: Cognitive Thinking Skills<br/>(28 mental model skills ported from portfolio)"]
    end
```

---

## Detailed Implementation Roadmap: Tracks 8, 9, 10

### Track 8: Scripts Indexer & Safety Scanner (`tools/scripts_index.py`)
- [x] Implement `tools/scripts_index.py` compliant with Appendix C specification:
  - `--build`: Scan `tools/` and user scripts directory, extract AST docstrings and argument signatures, determine safety classification, and write `config/scripts.json` (21 verified tools).
  - `--check`: Validate that every tool script in `tools/` is registered in `config/scripts.json`, has an assigned safety class (`workspace_write`, `read_only`, `network`, `sandbox`), and is not stale.
  - `--json`: Output machine-readable scripts catalogue.
- [x] Unit tests in `tests/test_scripts_index.py` (3 tests passing).

### Track 9: Taxonomized Skills Index & Cookbooks (`tools/skills_index.py` & `skills/**/cookbook.md`)
- [x] Implement `tools/skills_index.py`:
  - `--build`: Scan all `skills/**/SKILL.md`, categorize into domain taxonomy (Cognitive, Architecture, DevOps, Security, Research, Coding), compile `config/skills_index.json` (90 skills indexed).
  - `--check`: Verify all skills have valid YAML frontmatter, progressive disclosure sections, and associated executable cookbooks.
  - `--cookbook <skill>`: Output the executable step-by-step recipe tailored for `lfm3:1.7b` (preconditions, exact tool/CLI invocations, verification checkpoints, error remediation).
- [x] Author actionable `cookbook.md` files for all skills following the OpenMonoAgent pattern.
- [x] Unit tests in `tests/test_skills_index.py` (2 tests passing).

### Track 10: Cognitive Thinking Skills Portfolio Ingestion (`cc-thinking-skills`)
- [x] Port key mental model skills into `skills/` with YAML frontmatter triggers and OpenMonoAgent cookbooks:
  - `first-principles-thinking` (enriched with cookbook)
  - `inversion-thinking` (Carl Jacobi: Invert, always invert; solving backwards)
  - `pre-mortem-analysis` (Gary Klein: Prospective hindsight before breaking operations)
  - `occams-razor` (William of Ockham: Parsimony and speculative complexity pruning)
  - `second-order-thinking` (Howard Marks / Garrett Hardin: "And then what?" ripple effects)
  - `five-whys-root-cause` (Sakichi Toyoda: 5-whys systemic causal chaining)
  - `decision-matrix-analysis` (Stuart Pugh: Weighted MCDA multi-criteria evaluation)
  - `tradeoff-evaluator` (Thomas Sowell: Deliberate, bounded compromises)
  - `context-boundary-optimizer` (ICM: Progressive context disclosure L0-L3)
- [x] Connect turn context compiler (`agent/turn_context.py`) to `config/skills_index.json` with trigger phrase matching.
- [x] Unit and regression tests passing (`test_turn_context.py`, 614 agent tests passing, 0 ruff errors).

### Track 11: Inbox Note Promotion Engine (`tools/promote_inbox.py`)
- [x] Implement `tools/promote_inbox.py` (§7, §16 Decision 6):
  - `--list`: Inspect all quarantined units under `wiki/library/knowledge/_inbox/unsorted/` with extraction confidence score and inferred domain.
  - `--promote <file> --domain <target>`: Atomic move to target taxonomy directory, frontmatter status update (`status: reviewed`, `promoted_at: <ISO>`), and empty folder cleanup.
  - `--auto-source <slug|all> --domain <target>`: Batch promotion of all notes originating from a source book.
  - `--dry-run`: Preview promotion actions and path targets without disk modifications.
- [x] Unit tests in `tests/test_promote_inbox.py` covering listing, dry-run, invalid domain rejection, and real promotion lifecycle.
- [x] Execute promotion of the initial 89 quarantined companion book notes to `humanities/philosophy/eastern-philosophy/buddhism/` and sync second-brain search index (the folder now holds 265 notes after later phases).
- [x] Commit clean taxonomy state in Obsidian vault branch `taxonomy/spine-v1`.

### Track 12: Portfolio Skills Absorption (`ai_systems_repository_catalog.md`)
- [x] Absorb 15 high-leverage skills from portfolio repositories across 3 groups with YAML frontmatter triggers and OpenMonoAgent `cookbook.md` recipes:
  - **Group 1: Engineering Methodology & Execution**
    1. `systematic-debugging` (`superpowers`: 4-phase root-cause isolation and test reproduction)
    2. `structured-task-planning` (`superpowers`: DAG dependency-ordered atomic task breakdown)
    3. `spec-driven-development` (`spec-kit`: formal SDD design documents and invariant contracts)
    4. `sop-builder` (`procedure-ops`: standard operating procedure and runbook compiler)
    5. `simplified-technical-english` (`simplified-technical-english`: ASD-STE100 precision documentation)
    6. `adhd-optimized-communication` (`i-have-adhd`: bottom line upfront (BLUF) scannable formatting)
    7. `deterministic-code-audit` (`open-code-review`: pre-commit NPE, SQLi, and security static scan)
  - **Group 2: Advanced Cognitive & Strategic Models** (`cc-thinking-skills`)
    8. `pareto-principle-80-20` (80/20 leverage point discovery on bottlenecks and failure vectors)
    9. `chestertons-fence` (provenance audit: never remove legacy guards without understanding why)
    10. `hanlons-razor` (systemic attribution: eliminate benign configuration drift before assuming malice)
    11. `reversibility-two-way-doors` (Bezos Type 1 irreversible vs Type 2 reversible decision triage)
    12. `red-team-blue-team` (adversarial attack-and-patch simulation for security boundaries)
    13. `cynefin-framework` (Snowden complexity domain sense-making: Clear, Complicated, Complex, Chaotic)
  - **Group 3: Meta-Skill Distillation & Optimization**
    14. `skill-distillation` (`Resource2Skill`: extract SKILL.md and cookbook.md from raw guides)
    15. `skill-optimizer` (`SkillOpt`: text-space trajectory-driven prompt and recipe refinement)
- [x] Recompile `config/skills_index.json`: 39 verified skills across 6 academic domains.
- [x] Unit tests in `tests/test_turn_context.py` validating trigger shortlisting for all new skills (8 passed).

### Track 13: Full Portfolio Skills Absorption (Batch 2: 16 Skills)
- [x] Absorb 16 additional skills across Media, Office, Perception, Graph RAG, and Cognition:
  - **Media, Office & Artifact Generation**:
    1. `answer-me-with-html` (`answer-me-with-html`: self-contained single-page responsive HTML dashboards)
    2. `system-architecture-diagrammer` (`anatomy`: Mermaid and pure SVG system architecture diagrams)
    3. `open-codesign` (`open-codesign`: accessible HTML/CSS/Tailwind UI/UX rapid prototyping)
    4. `ffmpeg-media-processing` (`ffmpeg-skill`: deterministic clipping, transcoding, and -14 LUFS loudness leveling)
    5. `headless-office-automation` (`OfficeCLI`: programmatic DOCX, XLSX, and PPTX manipulation without Office)
    6. `cli-wrapper-generator` (`CLI-Anything`: wrap CLI binaries into agent-native tools with JSON outputs)
  - **Perception, Graph RAG & Memory**:
    7. `authenticated-browser-automation` (`BrowserSkill`/`ego-lite`: background browser automation with persistent session cookies)
    8. `agent-reach-scraper` (`Agent-Reach`: zero-API-cost public web, Reddit, and GitHub search and extraction)
    9. `code-graph-navigator` (`code-graph-rag`/`graphify`: AST-based caller/callee trees and monorepo symbol navigation)
    10. `memory-consolidation` (`mempalace`/`TencentDB-Agent-Memory`: consolidate session working memory into second brain)
  - **Advanced Cognitive & Analytical Models** (`cc-thinking-skills`):
    11. `circle-of-competence` (epistemic humility: strictly declare boundaries and refuse to hallucinate)
    12. `root-cause-fishbone` (Ishikawa 6M multi-factor defect breakdown: Machine, Method, Material, Manpower, Measurement, Milieu)
    13. `goodharts-law` (invariant audit against metric gaming and optimization pathologies)
    14. `opportunity-cost-evaluator` (economic trade-off evaluation of forgone alternatives and maintenance tails)
    15. `triangulation-validation` (3-channel verification of critical claims across code, runtime, and documentation)
    16. `parkinsons-law-compression` (force radical simplicity by imposing strict artificial turn, budget, and diff caps)
- [x] Recompile `config/skills_index.json`: **55 verified skills** across 6 academic domains.
- [x] Unit tests in `tests/test_turn_context.py` validating trigger shortlisting for new skills (10 passed).

### Track 14: OpenMonoAgent Playbooks Absorption (7 Skills -> 62 Total Skills)
- [x] Absorb 7 production playbooks from `OpenMonoAgent.ai` with progressive disclosure `SKILL.md` and concise executable `cookbook.md` recipes:
  1. `incident-response` (`OpenMonoAgent.ai` incident-response: calm, 6-step emergency triage, blast radius, health check verification, and blameless postmortem)
  2. `database-migration-ops` (`OpenMonoAgent.ai` db-migrate: zero-downtime schema migrations, pre-flight backup snapshots, dry-run SQL, reversible down scripts)
  3. `release-engineering` (`OpenMonoAgent.ai` release: end-to-end production release pipeline, pre-flight checks, changelog compilation, semver bumping, annotated git tagging)
  4. `pr-readiness` (`OpenMonoAgent.ai` pr-ready: branch pre-flight validation, upstream sync check, local test pass, linter zero-defect check, structured PR summary)
  5. `secret-and-leak-scanner` (`OpenMonoAgent.ai` file-scan & security guards: automated pre-commit scanning for private keys, high-entropy tokens, cloud credentials)
  6. `conventional-commit-generator` (`OpenMonoAgent.ai` commit: staged diff analysis, scope inference, SemVer type classification, under-72-char conventional commits)
  7. `blast-radius-analyzer` (`OpenMonoAgent.ai` code-review-graph: AST caller trees, transitive dependency graphs, downstream risk evaluation prior to breaking changes)
- [x] Recompile `config/skills_index.json`: **62 verified skills** across 6 academic domains.
- [x] Unit test in `tests/test_turn_context.py` (`test_openmono_playbooks_triggers_and_cookbooks`): 9/9 passed.
- [x] Full agent test suite: 614 passed, 4 skipped, 0 failed.

### Track 15: Portfolio & OpenMono Skills Absorption (Batch 4: 8 Skills -> 70 Total Skills)
- [x] Absorb 8 production skills across DevOps, Architecture, Coding, Research, and Cognition with YAML frontmatter triggers and OpenMonoAgent-style `cookbook.md` recipes:
  1. `server-deployment-ops` (`OpenMonoAgent.ai` server-deploy: host bridge contracts, docker compose zero-downtime rolling deploys, health verification)
  2. `llms-txt-generator` (`automatic-ai-experiments`: standardized `llms.txt` and `llms-full.txt` project documentation indices)
  3. `windows-gui-automation` (`Windows-Use`: desktop OS control via Win32 and UI Automation accessibility tree traversal)
  4. `autonomous-web-research` (`hyperresearch` / `WeKnora`: multi-query source triangulation and markdown research note synthesis)
  5. `scroll-driven-web-design` (`scroll-craft`: CSS animation-timeline view(), 60fps transform pinning, accessible fallbacks)
  6. `grounded-claim-verification` (`Astrolabe`: epistemic claim grounding, line-level file provenance hyperlinks, empirical verification)
  7. `desktop-app-instrumentation` (`mimic`: native desktop app IPC interception via localhost HTTP/WS/pipes and Python wrapping)
  8. `postgres-query-optimizer` (`pgbot`: EXPLAIN ANALYZE execution plan breakdown, buffer hit auditing, non-blocking CONCURRENT index deployment)
- [x] Recompile `config/skills_index.json`: **70 verified skills** across 6 academic domains.
- [x] Unit test in `tests/test_turn_context.py` (`test_batch4_skills_triggers_and_cookbooks`): 10/10 passed.

### Track 16: Portfolio Skills Absorption (Batch 5: 10 Skills -> 80 Total Skills)
- [x] Absorb 10 specialized procedural and multimodal skills with YAML frontmatter triggers and OpenMonoAgent-style `cookbook.md` recipes:
  1. `text-to-cad-modeling` (`text-to-cad`: parametric 3D CAD modeling, OpenSCAD/CadQuery CSG solids, manifold STL export)
  2. `book-to-skill-compiler` (`book-to-skill` / `Resource2Skill`: technical book distillation into actionable agent skills)
  3. `interactive-data-visualization` (`data-formulator`: declarative exploratory data analysis, Vega-Lite/ECharts interactive HTML charts)
  4. `collaborative-canvas-designer` (`doop`: infinite visual UI wireframing, semantic SVG layout hierarchy, design token grids)
  5. `multimodal-media-crawler` (`MediaCrawler`: social video subtitle harvesting, compressed audio demuxing, metadata sidecars)
  6. `sqlite-fts-optimizer` (second-brain FTS5 full-text search tuning, unicode61 diacritic stripping, BM25 weight calibration)
  7. `rest-api-contract-generator` (`spec-kit`: OpenAPI 3.1 contract-first API design, RFC 7807 error envelopes, mock endpoints)
  8. `circuit-breaker-resilience` (3-state finite state machine, exponential backoff with full jitter, concurrency bulkheads)
  9. `zero-downtime-canary-deploy` (progressive traffic splitting 2% -> 20% -> 100%, P99 latency monitoring, automated rollback)
  10. `epistemic-arbitrage` (Austrian economics & cognitive strategy: identifying asymmetric knowledge gaps between LLMs and local ground truth)
- [x] Recompile `config/skills_index.json`: **80 verified skills** across 6 academic domains.
- [x] Unit test in `tests/test_turn_context.py` (`test_batch5_skills_triggers_and_cookbooks`): 11/11 passed.

### Track 17: Portfolio Skills Absorption (Batch 6: 10 Skills -> 90 Total Skills) & HUD / CLI Integration
- [x] Absorb 10 high-impact cognitive and extraction skills:
  1. `smart-web-scraper-graph` (`Scrapegraph-ai` / `firecrawl`: AI-driven declarative DOM extraction with zero CSS selector brittleness)
  2. `graph-rag-hybrid-retriever` (`ragflow` / `semantica`: hybrid dense-sparse semantic graph retrieval with Reciprocal Rank Fusion)
  3. `structured-grammar-generator` (`outlines`: token logit masking and finite-state machine constrained decoding for 100% valid JSON)
  4. `answering-engine-synthesizer` (`Vane`: Perplexity-style citation-grounded answering with inline footnotes and bibliography)
  5. `universal-document-converter` (`anydoc` / `markitdown`: converting Word, PDF, ePub, PPTX, and XLSX files to clean Markdown)
  6. `test-suite-flakiness-hunter` (`superpowers`: condition-polling predicates, random-order execution, and flaky race condition elimination)
  7. `sunk-cost-fallacy-detector` (`cc-thinking-skills`: decoupling historical investment from prospective value to decommission dead code)
  8. `loss-aversion-calibrator` (`cc-thinking-skills`: symmetric expected value calculations to counter architectural change paralysis)
  9. `survivorship-bias-auditor` (`cc-thinking-skills`: Abraham Wald missing-dataset auditing to expose unlogged silent process crashes)
  10. `confirmation-bias-inversion` (`cc-thinking-skills`: Karl Popper falsification testing hunting for disconfirming negative test cases)
- [x] Recompile `config/skills_index.json`: **90 verified skills** across 6 academic domains (`cognitive`: 27, `coding`: 21, `devops`: 17, `research`: 11, `architecture`: 10, `security`: 4).
- [x] Unit test in `tests/test_turn_context.py` (`test_batch6_skills_triggers_and_cookbooks`): 12/12 passed.
- [x] **HUD Slash Commands**: Added `/domains` (skills breakdown by taxonomy domain) and `/brain <query>` (direct Second Brain FTS5 search from the chat box) in `core/text_turn_controller.py`.
- [x] **CLI Enhancement**: Added `--domain` filtering to `python -m agent skills` with colored domain badges in `agent/__main__.py`.
- [x] Unit tests in `tests/test_routing_authority.py`: 106/106 passed.
- [x] **Second Brain Maintenance**: Executed `sync(embed=False)` over 3,646 vault pages and optimized `chunks_fts` virtual table.
- [x] Full agent test suite: 614 passed, 4 skipped, 0 failed.

### Track 18: External Software Installation & Bootstrap Automation
- [x] **Audit & Installation of External CLI Software**:
  - `rg` (ripgrep 15.2.0): Installed in user environment via winget link (`BurntSushi.ripgrep.MSVC`).
  - `ffmpeg` & `ffprobe`: Installed in user environment via winget link (`yt-dlp.FFmpeg`).
  - `yt-dlp` (2026.10.26): Installed in user environment via winget link (`yt-dlp.yt-dlp`).
  - `pandoc` (3.13): Installed in user environment (`JohnMacFarlane.Pandoc`).
  - `sqlite3` (3.54.1): Installed in user environment via winget link (`SQLite.SQLite`).
  - `openscad` (2021.01): Resolved winget GUI installer hang by automating silent official portable zip installation to `%LOCALAPPDATA%\OpenSCAD` and binding to user `PATH` (verified headless 3D CAD to STL rendering in 0.001s).
  - Skipped Docker as explicitly requested by user.
- [x] **Setup & Bootstrap Integration**:
  - Created [`scripts/install_system_tools.ps1`](file:///C:/Users/Admin/Brahma-Ai-Evo/scripts/install_system_tools.ps1) with `-CheckOnly` flag and automated portable zip fallback for OpenSCAD.
  - Enhanced [`setup.py`](file:///C:/Users/Admin/Brahma-Ai-Evo/setup.py) to check and install all 6 recommended tools, dynamically refreshing Windows registry PATH across sessions.
  - Updated [`scripts/setup_dev.ps1`](file:///C:/Users/Admin/Brahma-Ai-Evo/scripts/setup_dev.ps1) to add Step 5 tool auditing and `-InstallSystemTools` parameter.
  - Updated [`scripts/install_optional_deps.ps1`](file:///C:/Users/Admin/Brahma-Ai-Evo/scripts/install_optional_deps.ps1) to include the `system-tools` group.

### Track 19: Skill-Selection Self-Check Loop (M1–M4, M6 implemented; M5 deferred)
Design record: [`docs/SKILL-SELECTION-SELF-CHECK-LOOP.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/SKILL-SELECTION-SELF-CHECK-LOOP.md).
Extracted from an external developer's description of his end-of-task routing
check; maps onto existing seams (TurnPlan → trace → `verify_work` → forge
staging → `agent eval`). **Local-first, no online JEV dependency.**

- [x] **M1** Persist a `SkillSelection` record per run at turn end (per-signal `signals`/`why`, `score_raw`, source + cookbook digests, `index_sha`, `winner_margin`, `retrieval.tier`, outcome evidence; joinable on `trace_id`). `agent/turn_context.py`, `agent/trace.py` (`SkillSelectionSink`), `agent/loop.py`.
- [x] **M2** Deterministic detector `agent/skill_audit.py` (R1 domain mismatch, R2 weak signal, R3 injected-but-unused, R4 failure-attributable, R5 source drift evaluated first) + `python -m agent skills audit [--since 7d] [--json]`. No model calls.
- [x] **M3** Diagnosis → staged fix diff (never applied; index fixes land in `skills/<n>/SKILL.md` + rebuild, proposals go to `staged_skills/_audit/`). `python -m agent skills audit --stage-fixes`; `skills/` is never written.
- [x] **M4** Replay corpus: `skill_choice` category in `agent/evals.py` + `evals/scenarios/skill_choice.json` (6 cases), with a neighbour guard on the top pick. `python -m agent eval --category skill_choice --provider stub`.
- [ ] **M5** Cover model-initiated loads (`load_project_skill`, `load_skill`) and surface a "Misses" digest. R1–R4 already cover every injected skill; the explicit call detector and the Settings → Skills tab are deferred.
- [x] **M6 (optional)** Local embedding retrieval (T2) — **local-first, no online JEV**: `tools/skills_index.py --build-embeddings` emits `config/skills_embeddings.json`; `agent/turn_context.py` takes an injected `embed` callable (default `second_brain.embed_query`) and reuses the same local `urllib` embedding pattern, in-memory cosine, `SKILL_COSINE_FLOOR=0.5`, `SKILL_EMBED_WEIGHT=0.35`. Gated on the measured R2 (`weak_signal`) rate and `BRAHMA_SKILL_EMBED=1`; T1 keyword scoring remains the fallback and the contract. T3 model tie-break deliberately not built.
- [ ] Open questions for the author (§11 of the doc) still unanswered — semantics of ground truth, checker cost, and negative feedback are guesses.
- [x] Measured gate for this track: `python -m ruff check .` clean; `$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests -q -k "agent or trace or turn_context or skills_index or skill_audit"` → 710 passed, 3 skipped in 112 s.

### Verification Gate (AGENTS.md)
- [x] `python -m ruff check .` -> All checks passed (0 errors).
- [x] `$env:QT_QPA_PLATFORM="offscreen"; python -m pytest tests -q` -> 1,716 passed, 7 skipped, 0 failed.
- [x] Stub provider verification: `python -m agent run "say hello" --provider stub --json --no-session` -> clean execution.
- [x] Architecture ratchets clean (`core/tool_exec.py` 0 `elif` branches, line ceilings respected).
