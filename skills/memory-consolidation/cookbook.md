# Cookbook: Consolidating Session Working Memory into Second Brain

## Objective
Distill a completed agent session transcript into durable, structured memory notes and refresh the second-brain search index.

## Prerequisites
- Completed session transcript or conversation history in `agent/session.py`.
- Obsidian second-brain vault.

## Execution Recipe
1. **Extract Durable Decisions and Preferences**:
   Scan transcript turns for:
   - System configurations adopted (e.g. "always use Python 3.11 for gates").
   - User project decisions and architectural dispositions.
   - Newly discovered book/concept relationships.
2. **Draft Atomic Concept or Preference Note**:
   Create Markdown note under `wiki/library/knowledge/` or `wiki/decisions/`:
   ```markdown
   ---
   type: DecisionNote
   title: "Session Resumption Invalidation on 1007"
   domain: applied-sciences/engineering-and-technology
   status: reviewed
   date: 2026-11-10
   ---
   # Decision: Invalidate Resumption Handle on 1007 Error
   ...
   ```
3. **Re-Index Second Brain Search**:
   ```powershell
   python tools/brain_sync.py --no-embed
   ```
4. **Verification Gate**:
   Query the newly consolidated memory via `python -m agent run "brain_search query='<topic>'"` and confirm retrieval.
5. **Remediation**:
   If index shows stale entries, run `python tools/brain_sync.py --rebuild` for a fresh SQLite FTS5 rebuild.
