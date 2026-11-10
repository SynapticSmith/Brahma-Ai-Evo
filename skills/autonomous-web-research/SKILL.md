---
name: autonomous-web-research
description: Autonomous multi-query web research, evidence triangulation, source fact-checking, and structured markdown synthesis for second-brain knowledge capture.
metadata:
  domain: research
  tags: [research, web-search, knowledge-capture, second-brain, synthesis, triangulation]
triggers:
  - "autonomous web research"
  - "research topic online"
  - "synthesize web research"
  - "deep web investigation"
  - "triangulate web sources"
  - "compile research note"
---

# Autonomous Web Research Skill

## Overview
Drawn from `hyperresearch` and `WeKnora`, this skill governs deep, agentic information gathering across public web sources. Shallow search queries yield fragmented, redundant, or SEO-hallucinated results. This skill enforces a structured research protocol: decompose ambiguous topics into targeted search queries, scrape authoritative primary documentation, triangulate findings across multiple independent domains, and synthesize structured, citation-backed markdown notes.

## Methodological Invariants
1. **Multi-Query Decomposition**: Never rely on a single keyword query. Decompose the research objective into 3 distinct angles: Architecture/Mechanism, Practical Examples, and Known Failure Modes.
2. **Primary Source Priority**: Prioritize official documentation, GitHub repositories, peer-reviewed whitepapers, and RFC standards over secondary blog summaries.
3. **Triangulation Rule**: Extraordinary claims or benchmark numbers require verification across at least 2 independent sources before entering permanent notes.
4. **Permanent Attribution**: Every extracted claim must record its source URL, author/org, and publication timestamp.

## Execution Sequence
1. **Query Planning**: Formulate 3-5 search queries exploring core definitions, edge cases, and comparisons.
2. **Web Retrieval**: Execute search queries and ingest clean text from top candidate pages.
3. **Cross-Examination**: Compare conflicting claims and identify consensus vs contested points.
4. **Synthesize Wiki Note**: Author structured markdown with Overview, Technical Details, Trade-offs, and Citations.
5. **Knowledge Placement**: File into the appropriate taxonomy directory (`wiki/library/knowledge/...`).
