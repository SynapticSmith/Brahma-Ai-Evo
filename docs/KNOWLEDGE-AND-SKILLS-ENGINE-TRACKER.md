# Knowledge & Skills Engine — Implementation Progress Tracker

**Document Status:** Active Progress Tracker  
**Authority:** Tracks implementation of [`docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md) (Rev 3.1)  
**Created:** 2026-11-09  
**Last Updated:** 2026-11-09  

---

## 1. High-Level Phase Status

| Phase | Description | Status | Deliverables | Exit Criteria Met? |
|:---|:---|:---:|:---|:---:|
| **Phase 0** | Audit & Snapshot (read-only) | 🟢 **COMPLETED** | `_audit/taxonomy-audit.md`, `taxonomy.json`, vault tag/branch `taxonomy/audit-v1` | [x] Passed |
| **Phase 1** | Normalize Tree & Transplant Notes | 🟡 **IN PROGRESS** | `taxonomy-migration.json`, `_audit/link-rewrite.json`, `tools/taxonomy.py` | [ ] Pending |
| **Phase 2** | Wikipedia Knowledge Tree Seeding | ⚪ Queued | `tools/seed_academic_taxonomy.py`, cached JSON/MD in `raw/wikipedia/` | [ ] Pending |
| **Phase 3** | Retrieval Schema v2 & Type-Weighted Ranking | ⚪ Queued | SQLite schema migration, type-weighted FTS5, `max 2 index pages` rule | [ ] Pending |
| **Phase 4** | Multimodal Conversion & OCR Pipeline | ⚪ Queued | `tools/ingest_multimodal.py`, `markitdown` + `Unlimited-OCR` integration | [ ] Pending |
| **Phase 5** | Knowledge Extraction & Placement | ⚪ Queued | `tools/extract_knowledge.py`, segmentation, atomization, `_inbox/` routing | [ ] Pending |
| **Phase 6** | JIT Skills & Scripts Engine | ⚪ Queued | `TurnPlan` compiler, L2 SOP injection, `tools/scripts_index.py` | [ ] Pending |
| **Phase 7** | Teacher Escalation & Git Worktree Harness | ⚪ Queued | `tools/worktree.py`, policy layer over `skill_forge` & `skill_crucible` | [ ] Pending |

---

## 2. Phase-by-Phase Task Breakdown & Checklist

### Phase 0: Audit & Snapshot (Read-Only Vault Operations) — 🟢 COMPLETED
- [x] Create vault backup tag `vault-pre-taxonomy-v1` and branch `taxonomy/audit-v1` in `C:\Users\Admin\Documents\Obsidian\My Vault`.
- [x] Build automated audit script `tools/audit_vault_taxonomy.py` to inspect `wiki/library/knowledge/`.
- [x] Generate comprehensive audit report: `C:\Users\Admin\Documents\Obsidian\My Vault\wiki\library\knowledge\_audit\taxonomy-audit.md`.
  - [x] Count and list all 2,520 stub notes (< 200 chars).
  - [x] Extract and catalog all 321 substantive authored notes to be preserved (`_audit/substantive-notes.json`).
  - [x] Map all 425 duplicate directory names and recursive misplaced paths.
  - [x] Record all 54 inbound links from 16 files across the vault (`_audit/inbound-links.json`).
- [x] Synthesize initial canonical `taxonomy.json` v1 (5 divisions, 122 canonical spine nodes with legacy domain aliases) in `config/taxonomy.json` and `_taxonomy/taxonomy.json`.
- [x] Create initial human overrides file `_taxonomy/taxonomy.overrides.yaml`.
- [x] Verify zero vault mutations occurred during audit. Exit criteria passed.

### Phase 1: Normalize Existing Tree & Transplant Substantive Notes
- [ ] Build `tools/taxonomy.py` with CLI flags: `--check`, `--lint-notes`, `--check-links`, `--plan`, `--apply`, `--prune`.
- [ ] Generate migration plan: `taxonomy-migration.json` and `_audit/link-rewrite.json`.
- [ ] Scaffold the clean 5-division spine under `wiki/library/knowledge/` (`humanities/`, `social-sciences/`, `natural-sciences/`, `formal-sciences/`, `applied-sciences/`).
- [ ] Transplant the ~320 substantive notes into their exact canonical subdiscipline destinations.
- [ ] Prune empty/stub directories from old structure.
- [ ] Rewrite all 40 path-form inbound links using `_audit/link-rewrite.json`.
- [ ] Verify zero broken links with `python tools/taxonomy.py --check-links`.
- [ ] Rebuild second brain index: `python tools/brain_sync.py --no-embed`.

### Phase 2: Wikipedia Knowledge Tree Seeding
- [ ] Implement `tools/seed_academic_taxonomy.py` with caching in `raw/wikipedia/`.
- [ ] Fetch Wikipedia lead sections, definitions, subdisciplines, and major theories for each canonical node.
- [ ] Format node index pages (`index.md`) with canonical OKF v0.2 frontmatter, CC BY-SA 4.0 attribution, and L0→L3 content sections.
- [ ] Enforce depth cap of 6 (collapse depth-7 nodes into parent concept-hub notes).
- [ ] Verify ≥ 95% node coverage and offline re-run idempotency.

### Phase 3: Retrieval Schema v2 & Type-Weighted Ranking
- [ ] Implement SQLite schema migration (`SCHEMA_VERSION = 2`) in `agent/second_brain.py`.
- [ ] Add `kind` (index, concept, book, synthesis, entity), `domain`, and `quality` columns.
- [ ] Implement type-weighted BM25 scoring in FTS5 search (prioritize concept and book notes over index pages).
- [ ] Enforce "max 2 index pages per turn" in `context_for_turn()`.
- [ ] Implement selective embedding policy: exclude generic index pages by default to protect CPU/embedding budget.
- [ ] Run knowledge routing evaluation suite to confirm recall improvement.

### Phase 4: Multimodal Conversion & OCR Pipeline
- [ ] Build `tools/ingest_multimodal.py` extending `operations/book-library/` and `book-library/fulltext/`.
- [ ] Integrate Microsoft MarkItDown (`C:\Users\Admin\markitdown`) for native digital formats (DOCX, PDF, EPUB, XLSX).
- [ ] Integrate Unlimited-OCR (`C:\Users\Admin\Unlimited-OCR`) for complex layouts, math/LaTeX formulas, and scans.
- [ ] Extract visual assets (diagrams, figures, flowcharts) to `fulltext/.assets/<slug>/` with markdown cross-references.
- [ ] Generate `.meta` manifests for every converted document.
- [ ] Run 10-document conversion test fixtures offline.

### Phase 5: Knowledge Extraction & Placement
- [ ] Build `tools/extract_knowledge.py`.
- [ ] Implement deterministic segmentation and candidate proposition extraction.
- [ ] Build academic taxonomy classifier routing units into leaf subdisciplines.
- [ ] Implement confidence thresholding: high confidence &rarr; canonical leaf node; low confidence &rarr; `_inbox/`.
- [ ] Formulate extracted notes with OKF frontmatter and Calyx constellation anchoring.
- [ ] Test on hand-labelled chapter fixtures.

### Phase 6: JIT Skills & Scripts Engine
- [ ] Define standardized `TurnPlan` dataclass in `agent/context.py` and `agent/loop.py`.
- [ ] Extend `core/skills_library.py` with domain taxonomy, L0 metadata indexing, and L2 SOP retrieval.
- [ ] Build `tools/scripts_index.py` generating `scripts/index.json` for all CLI tools.
- [ ] Implement intent interpreter in `AgentLoop._prepare_turn` injecting task-specific L2 SOP into ephemeral request context.
- [ ] Port 10 high-impact skills and 3 cookbooks from portfolio repositories.
- [ ] Pass skill-selection gold-set acceptance tests.

### Phase 7: Teacher Escalation & Git Worktree Harness
- [ ] Implement `tools/worktree.py` providing isolated Git worktree management over `agent.process.run_captured`.
- [ ] Build policy layer connecting execution gaps to `core/skill_forge.py` and `core/skill_crucible.py`.
- [ ] Implement sandboxed capability synthesis: Teacher model (Claude/Gemini) writes script & tests &rarr; worktree runs tests &rarr; AST check passes &rarr; user approval required before install.
- [ ] Add approval UI in desktop GUI / CLI runner.
- [ ] Demonstrate end-to-end create &rarr; verify &rarr; merge &rarr; rollback worktree cycle.

---

## 3. Implementation Log & Milestones

### 2026-11-09
- **Milestone:** Rev 3.1 of [`docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md) reviewed, verified, and adopted.
- **Milestone:** Created progress tracker in `docs/KNOWLEDGE-AND-SKILLS-ENGINE-TRACKER.md`.
- **Milestone:** Initiating Phase 0: Audit & Snapshot.
