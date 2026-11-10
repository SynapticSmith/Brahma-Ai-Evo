# Cookbook: Book to Knowledge Ingestion

## Objective
Convert an unprocessed book (EPUB, PDF, DOCX) into clean Markdown, extract atomic knowledge units, and route them to the academic taxonomy spine with Calyx anchoring.

## Prerequisites
- python 3.11+
- tools/ingest_multimodal.py
- tools/extract_knowledge.py

## Execution Recipe
1. **Convert Raw Book to Markdown**:
   ```powershell
   python tools/ingest_multimodal.py --source <path-to-book> --slug <book-slug>
   ```
2. **Extract Atomic Knowledge Units**:
   ```powershell
   python tools/extract_knowledge.py --source book-library/fulltext/<book-slug>.md --taxonomy taxonomy.json
   ```
3. **Commit Calyx Anchors**:
   Record cryptographic ledger constellations for all extracted units:
   ```powershell
   python tools/calyx_sync.py
   ```
4. **Verification Gate**:
   Check that units are placed in `wiki/library/knowledge/` and index is refreshed:
   ```powershell
   python tools/brain_sync.py
   ```
5. **Remediation**:
   If low confidence occurs, review orphaned notes staged in `_inbox/` and manually tag appropriate parent node.
