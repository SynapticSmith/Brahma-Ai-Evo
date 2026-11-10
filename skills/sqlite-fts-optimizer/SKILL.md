---
name: sqlite-fts-optimizer
description: SQLite FTS5 full-text search tuning, BM25 tokenizer optimization, index integrity verification, and zero-downtime database optimization for second-brain retrieval.
metadata:
  domain: architecture
  tags: [sqlite, fts5, search, bm25, second-brain, indexing, database, architecture]
triggers:
  - "sqlite fts optimizer"
  - "optimize sqlite search"
  - "tune fts5 index"
  - "bm25 ranking tuning"
  - "sqlite fts5 maintenance"
  - "rebuild second brain index"
  - "fts5 search tuning"
---

# SQLite FTS5 Search Optimizer Skill

## Overview
Drawn from Brahma's internal second-brain retrieval engine and SQLite FTS5 standards, this skill governs the design, tuning, and maintenance of high-speed local search databases. As personal vaults and code repositories grow past thousands of documents, poorly configured full-text indexes suffer from index fragmentation, vocabulary bloat, and skewed BM25 relevance scores. This skill enforces optimal tokenizers (Porter stemmer, unicode61), unaccent diacritic stripping, custom BM25 weights, and scheduled index optimization commands.

## Indexing Invariants
1. **Always Use unicode61 with Diacritic Stripping**: Tokenize using `tokenize='unicode61 remove_diacritics 2'` to ensure case-insensitive and accent-insensitive matching.
2. **Column Weight Calibration**: Calibrate BM25 column rank weights so title matches rank higher than body chunks (e.g. `bm25(fts_table, 10.0, 5.0, 1.0)`).
3. **Regular Maintenance Merge**: Run `INSERT INTO fts_table(fts_table) VALUES('optimize');` periodically to merge b-tree segments and shrink database footprint.
4. **Content Table Synchronization**: Use SQLite external content tables with `UNINDEXED` columns for IDs and timestamps to avoid storing duplicated text in memory.

## Tuning Strategy
1. **Schema Check**: Confirm virtual table uses `USING fts5(...)` with prefix indexes enabled (`prefix='2 3'`).
2. **Integrity Check**: Execute `INSERT INTO fts_table(fts_table) VALUES('integrity-check');` to guarantee b-tree consistency.
3. **BM25 Tuning**: Test query terms with `bm25()` rank scoring; verify relevance ordering across test corpus.
4. **Vacuum & Optimize**: Consolidate database files with `VACUUM;` and `PRAGMA optimize;`.
