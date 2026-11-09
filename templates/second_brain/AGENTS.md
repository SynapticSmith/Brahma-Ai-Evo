# AGENTS.md — Agent Operating Schema & System Instructions

You are the maintainer and consumer of this personal knowledge vault. The vault serves as an **autonomous agent second brain** and persistent knowledge base.

It reconciles:
1. **The LLM Wiki** (`wiki/`) — living, agent-maintained knowledge.
2. **Interpretable Context Methodology (ICM)** — five-layer progressive disclosure (L0→L4).
3. **Open Knowledge Format (OKF v0.2)** — structured, machine-queryable frontmatter.

---

## The Cheap Exit Clause (Token Conservation)

> [!IMPORTANT]
> **Do NOT consult or crawl the wiki for generic software development, syntax lookups, local script execution, or isolated debugging tasks.**
> If the user's task does not explicitly involve vault knowledge, project history, or wiki synthesis, exit the wiki routing ladder immediately to conserve context tokens.

---

## Layout & Shelf Architecture

```
.
├── README.md                 # L0: orientation for any harness
├── CONTEXT.md                # L1: root routing table ("I want X → go here")
├── AGENTS.md                 # this file — operating schema and agent instructions
├── raw/                      # append-only source documents (never rewritten)
├── wiki/                     # generated, owned, and maintained by agents
│   ├── index.md              # catalog of all pages
│   ├── log.md                # append-only timeline of operations
│   ├── hot.md                # L2: dynamic working memory brief
│   ├── entities/             # people, organizations, tools, products
│   ├── concepts/             # ideas, frameworks, canonical definitions
│   └── synthesis/            # cross-cutting analysis, comparisons, system maps
└── Personal notes/           # human-authored scratchpad and project notes
```

---

## Progressive Context Ladder (ICM Navigation)

When consulting the vault for domain knowledge or project state, follow the 5-layer ladder:
1. **L0 (Orientation):** `README.md` and this `AGENTS.md`.
2. **L1 (Routing):** `CONTEXT.md` — identify the responsible shelf or domain.
3. **L2 (Working Memory Brief):** Read `wiki/hot.md` for live status.
4. **L3 (Canonical Knowledge):** Read relevant concept pages in `wiki/concepts/` or `wiki/synthesis/`.
5. **L4 (Grounding & Verification):** Verify claims against sources in `raw/` or via Calyx anchors.

---

## Page Conventions

- **Filename:** kebab-case, `.md`. Filenames are stable; do not rename without updating backlinks.
- **Frontmatter:** OKF-style YAML at the top of every wiki page:
  ```yaml
  ---
  title: Human-readable title
  description: "One-line summary for catalog generation and previews"
  type: entity | concept | synthesis | source
  tags: [tag-a, tag-b]
  sources: [raw/source-file.md]
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  ---
  ```
- **Structural Exemptions:** `wiki/index.md`, `wiki/log.md`, and `wiki/hot.md` are structural files and carry no frontmatter.
- **Cross-links:** Obsidian-style `[[wiki/entities/some-page]]` (or bare `[[some-page]]` when unambiguous).
- **Citations:** Cite inline whenever a claim comes from a source: `(see [[wiki/sources/some-slug]])` or footnotes `[^1]`.
- **Voice:** Terse, factual, neutral. Reference material, not conversational narrative.
