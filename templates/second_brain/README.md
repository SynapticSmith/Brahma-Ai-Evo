# Brahma Second Brain — START HERE

A personal knowledge vault and autonomous agent second brain: an agent-maintained
wiki following the **Interpretable Context Methodology (ICM)** and **Open Knowledge Format (OKF v0.2)**.
Plain Markdown in folders — **no proprietary runtime is required**. Any agent or harness can navigate it from this file.

## The Entry Chain

1. **`README.md`** (this file) — L0 orientation, shelf map, standard conventions.
2. **`CONTEXT.md`** — L1 routing table: "I want to do X → go here."
3. **`AGENTS.md`** — The operating schema for agents (frontmatter format, citations, link rules, cheap exit).
4. **`wiki/hot.md`** — L2 dynamic working memory brief (recent log, active standups).

## Shelf Map — What Lives Where

| Path | Role | Status |
|------|------|--------|
| `README.md` | L0 orientation | canonical |
| `CONTEXT.md` | L1 root routing table | canonical |
| `AGENTS.md` | wiki operating schema & agent rules | canonical |
| `raw/` | append-only source documents (never rewritten) | source shelf |
| `wiki/` | the LLM Wiki (index, log, hot.md, entities, concepts, synthesis) | living knowledge |
| `Personal notes/` | human scratchpad & project notes | user workspace |

## Conventions (Short Form)

- **Routing vs Content:** Routing files (`README.md`, `CONTEXT.md`) point; shelves (`wiki/`, `raw/`, `Personal notes/`) hold content.
- **Cheap Exit:** Do not crawl or search the vault for generic software development, syntax lookups, or local command runs.
- **Frontmatter:** Wiki pages carry OKF-style YAML (`title`, `description`, `type`, `tags`, `sources`, `created`, `updated`).
- **Links:** Obsidian-style `[[wiki/entities/some-page]]` or `[[some-page]]` when unambiguous.
