# Brahma Second Brain & Calyx Grounding Guide

This guide explains how Brahma's second brain operates, how it reconciles **Karpathy's LLM Wiki** pattern with the **Interpretable Context Methodology (ICM)** and **Open Knowledge Format (OKF v0.2)**, how **Calyx** provides cryptographic provenance, and how any user can set up and maintain their own vault.

---

## 1. Architectural Philosophy: The Second Brain is Retrieved, Not Requested

A small local model (such as `lfm3:1.7b` on Ollama) cannot reliably remember tool names, select search queries, or decide that personal notes are needed. If knowledge requires explicit tool calls, a small model will frequently hallucinate before ever calling a search tool.

In Brahma:
1. **Pre-Turn Retrieval:** Before the first model turn of any request, `AgentLoop._prepare_retrieval` searches the second brain index for pages relevant to the user's turn.
2. **Context Injection:** The retrieved notes are formatted into a bounded block and injected immediately **before the most recent user message** in `as_payload()`.
3. **No Transcript Bloat:** The block is request-time context, not conversation transcript. It is never persisted, replayed, or compacted into historical turns.
4. **Model Agnostic:** Works identically for local models, custom endpoints, OpenRouter, and Gemini text surfaces. (Gemini Live voice surfaces receive the resident map and active function-calling tools).

---

## 2. The Four Standards That Meet Here

| System | Standard / Pattern | What it Does |
|---|---|---|
| **LLM Wiki** | Karpathy's LLM Wiki Pattern | Living, agent-maintained wiki (`wiki/`): raw intake &rarr; entities/concepts &rarr; cross-cutting synthesis. |
| **Interpretable Context Methodology (ICM)** | 5-Layer Progressive Disclosure | Folder hierarchy as agent architecture: L0 (`README.md`) &rarr; L1 (`CONTEXT.md`) &rarr; L2 (`hot.md`) &rarr; L3 (`wiki/concepts/`) &rarr; L4 (`raw/`). |
| **Open Knowledge Format (OKF v0.2)** | OKF Frontmatter Standard | Machine-queryable YAML frontmatter (`title`, `description`, `type`, `tags`, `sources`, `created`, `updated`). |
| **Calyx Grounding Engine** | Association-Native Proof Plane | Deterministic multi-lens database and hash-chained tamper-evident ledger for claims, anchors, and provenance. |

---

## 3. Quickstart: Initializing Your Own Second Brain

Any user who downloads Brahma can initialize their own second brain in seconds:

```powershell
# 1. Initialize a new vault from the Brahma template
python tools/init_second_brain.py --dest "$HOME\Documents\Obsidian\My Vault"

# 2. Check the status of your index
python tools/brain_sync.py --status
```

Then open **Obsidian**, click **Open folder as vault**, and select the destination directory.

### Configuration & Environment
Brahma checks for the vault path in this order:
1. `--vault <path>` CLI argument
2. `BRAHMA_SECOND_BRAIN_VAULT` environment variable
3. `%LOCALAPPDATA%\BrahmaAI\second_brain\config.json`
4. Default: `~/Documents/Obsidian/My Vault`

---

## 4. Shelf Architecture

```
My Vault/
├── README.md                 # L0: orientation for any agent/harness
├── CONTEXT.md                # L1: root routing table ("I want X → go here")
├── AGENTS.md                 # Operating schema, OKF frontmatter rules, cheap-exit
├── raw/                      # Append-only source shelf (PDFs, book dumps, web captures)
├── wiki/                     # Generated and maintained by agents
│   ├── index.md              # Catalog of all pages
│   ├── log.md                # Append-only chronological operations log
│   ├── hot.md                # L2: dynamic working memory brief (standups, active focus)
│   ├── entities/             # People, organizations, software tools, companies
│   ├── concepts/             # Ideas, frameworks, canonical definitions
│   └── synthesis/            # Comparative analysis, architecture maps, theses
└── Personal notes/           # Human scratchpad and project notes
```

### The Cheap-Exit Rule
To conserve context tokens, agents must follow the **Cheap Exit Rule**:
> Never crawl or search the vault for generic software development, syntax lookups, local command runs, or isolated debugging tasks. Exit the wiki ladder immediately if the user's turn does not touch vault knowledge.

---

## 5. Ingestion Pipelines: From Books to Knowledge

When processing books, papers, or Calibre exports:
1. **Raw Storage:** Place source text or dumps in `raw/` or `book-library/` (e.g. `raw/author/book-title.txt`).
2. **Normalization:** The intake script cleans whitespace, removes artifact headers, and creates chapter slices.
3. **OKF Concept Extraction:** The agent reads the chapters and creates concept notes in `wiki/concepts/` or `wiki/library/knowledge/<domain>/`.
4. **Citation:** The resulting note carries sources in its YAML frontmatter:
   ```yaml
   ---
   type: Concept
   title: "Anatta as 'Not the True-Self'"
   description: "Ken Lee Wheeler's reading of anatta as an adjective predicated of phenomena..."
   tags: [humanities, philosophy, buddhism, anatta]
   sources: [raw/Ken Lee Wheeler/The Core of Original Buddhism.txt]
   created: 2026-10-10
   ---
   ```

---

## 6. Calyx Integration: The Cryptographic Grounding Plane

While Obsidian stores human-readable descriptive knowledge, **Calyx** (`C:\Users\Admin\Calyx`) provides the **tamper-evident proof plane**:
- Stored as multi-lens **constellations** (sparse SPLADE, dense embeddings, entities).
- Verified facts and approved outcomes are **anchored** (`kind: "label", value: true`).
- Every measurement, answer, and anchor is recorded in a **hash-chained ledger** (`verify_chain`).

### Syncing Vault Notes to Calyx
```powershell
# Sync core wiki entities, concepts, and synthesis notes into Calyx
python tools/calyx_sync.py

# Ingest a specific shelf into Calyx
python tools/calyx_sync.py --shelf wiki/library/knowledge/humanities/philosophy/buddhism

# Check Calyx status (abundance, kernel recall, chain integrity)
python tools/calyx_sync.py --status
```

### Dual-Engine Context in Action
When you chat with Brahma, queries matching both Obsidian notes and Calyx constellations automatically receive both:
```markdown
SECOND BRAIN — notes retrieved for this turn:
[1] wiki/entities/calyx.md › Architecture
[2] Calyx/calyx/separation-of-concerns.md › Rule 5

CALYX GROUNDED PROVENANCE (tamper-evident hash-chained ledger):
• [constellation cb51b1721f2c…] ledger_seq=60 chain_hash=d3befcd1cbce0963… (anchored fact)
• [constellation e5c805be0d49…] ledger_seq=78 chain_hash=61b1a60648816ef2… (anchored fact)
```
The AI has access to the full conceptual explanation and mathematical proof that the claims are anchored and authentic.
