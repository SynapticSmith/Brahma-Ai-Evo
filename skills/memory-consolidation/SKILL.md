---
name: memory-consolidation
description: Long-term memory hygiene consolidating daily conversational ephemera into durable, indexed second-brain concept notes while decaying noise.
metadata:
  domain: architecture
  tags: [memory, consolidation, second-brain, rag, hygiene, decay, persistence]
triggers:
  - "memory consolidation"
  - "consolidate memories"
  - "prune memory noise"
  - "long term memory sync"
  - "memory hygiene"
---

# Memory Consolidation Skill

## Overview
Drawn from `mempalace` and `TencentDB-Agent-Memory`, this skill implements the biological phenomenon of memory consolidation in AI agents. Instead of letting raw, unstructured chat transcripts accumulate into an unindexable swamp, this skill runs periodic consolidation cycles:
1. **Filter Ephemera**: Discard transient chatter ("thank you", "hello", temporary debugging paths).
2. **Extract Semantic Facts**: Distill user preferences, architectural decisions, and project milestones into structured atomic notes.
3. **Link into Second Brain**: Place distilled notes into the academic taxonomy or `wiki/` directory with bidirectional wikilinks.
4. **Update Search Index**: Re-index the SQLite FTS5 database so consolidated knowledge is reachable across future sessions.
