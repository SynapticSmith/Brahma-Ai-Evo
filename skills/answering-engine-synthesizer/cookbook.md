# Cookbook: Perplexity-Style Citation-Grounded Answer Synthesis

## Objective
Synthesize an authoritative research answer with inline numbered citations and a complete reference bibliography.

## Prerequisites
- Retrieved context passages with associated source titles and URLs or file paths.

## Execution Recipe
1. **Assign Monotonic Identifiers to Context Chunks**:
   ```
   [1] "SQLite FTS5 uses BM25 scoring with configurable column weights." (docs/sqlite.md)
   [2] "Porter stemmer normalizes inflections like 'running' to 'run'." (docs/nlp.md)
   ```
2. **Draft Response with Inline Citations**:
   Write focused prose referencing the source indices:
   ```markdown
   SQLite full-text search implements the BM25 probabilistic ranking function [1].
   Search terms are automatically reduced to linguistic roots via Porter stemming [2].
   ```
3. **Compile Footnote References Section**:
   ```markdown
   ## References
   - [1] `docs/sqlite.md` — SQLite FTS5 Architecture Reference
   - [2] `docs/nlp.md` — Natural Language Tokenization Guide
   ```
4. **Verification Gate**:
   *Gate: Every cited number `[X]` appears in `## References`; zero orphaned citations.*
5. **Remediation**:
   If a claim lacks direct source proof, remove the claim or qualify it with "Hypothesis (unsourced)".
