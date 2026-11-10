---
name: book-to-knowledge
description: Autonomous pipeline converting raw technical books, PDFs, and EPUBs into structured, atomized OKF notes placed in the academic knowledge tree.
metadata:
  domain: applied-sciences/computing
  tags: [ingestion, extraction, books, knowledge, okf, second-brain]
triggers:
  - "ingest book"
  - "extract book"
  - "book to knowledge"
  - "convert pdf to notes"
---

# Book-to-Knowledge Skill

## Overview
Automates the conversion of complex literature (EPUB, PDF, DOCX) into atomic, machine-retrievable knowledge notes integrated into the Obsidian Second Brain and Calyx constellation graph.

## Pipeline Architecture
1. **Multimodal Conversion (`tools/ingest_multimodal.py`)**:
   - Parses document structure with MarkItDown.
   - Evaluates PDF glyph density and OCR necessity.
   - Extracts embedded diagrams and figures to `.assets/<slug>/`.
   - Generates `.meta/<slug>.json` manifest with SHA-256 integrity hash.
2. **Autonomous Knowledge Extraction (`tools/extract_knowledge.py`)**:
   - Segments text into bounded 400–4,000 character thematic sections.
   - Matches token BM25 similarity against the 122 canonical academic taxonomy leaves.
   - High confidence (>= 0.50): Routes to canonical spine directory with OKF frontmatter.
   - Low confidence (< 0.50): Routes to `_inbox/unsorted/<source-slug>/` for review.
   - Anchors propositions to Calyx constellation graph.
3. **Index Refresh**:
   - Updates SQLite FTS5 database to enable instant conversational recall.

## Execution Procedure
1. Place source file in working directory or library.
2. Run ingestion:
   ```powershell
   python tools/ingest_multimodal.py "path/to/book.epub"
   ```
3. Run extraction:
   ```powershell
   python tools/extract_knowledge.py "book-library/fulltext/<slug>.md" --calyx
   ```
4. Verify placement and check for any notes routed to `_inbox/`.
