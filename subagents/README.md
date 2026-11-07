# Vendored subagent library

This directory is the default specialist-template corpus for the Brahma agent
harness. It is **data**, not code: nothing here is imported, and no module
should hard-code a slug or read a body except through
`agent/subagent_library.py`.

## Provenance

Ported from the Obsidian vault folder
`My Vault/subagent-orchestration`, which was itself adapted from the
610ClaudeSubagents corpus on 2026-10-10. The vault does not travel with this
repository, so the catalogue and every body it names are committed here.
`CLAUDE.md`, `taxonomy.md`, `promoted.md`, `duplicates.md`, `orchestration/`
and `convert-import-script.ps1` are retained for provenance and human
navigation; the harness reads only `catalog.json` and the `path` of each row.

## Layout

| Path | What it is |
| --- | --- |
| `catalog.json` | 610 rows (607 agents, 3 docs): slug, category, one-line description, tools, path. This is the index of record. |
| `agents/` | One markdown body per catalogue row. Frontmatter is stripped when a body is loaded. |
| `taxonomy.md` | Human-readable category view. |
| `orchestration/` | Multi-agent flow patterns and authoring notes. |

## How the harness uses it

Tiered disclosure keeps the corpus out of the resident prompt:

1. `agent/subagent_library.py` builds a ~1.4 KB category index (counts plus a
   few example slugs) for the system prompt.
2. `list_subagents(query=…, category=…)` returns slugs and one-line
   descriptions.
3. `run_subagent(subagent_type="<slug>", task="…")` loads exactly one body
   (clamped to 24 KB) into the child's brief.

Resolution order is `BRAHMA_SUBAGENT_LIBRARY`, `config/subagents.json`, this
directory, then the old Obsidian location as a compatibility fallback. An empty
`BRAHMA_SUBAGENT_LIBRARY` disables the library. See `docs/AGENTIC-HARNESS.md`
and `AGENTS.md`.

## Updating

Replace `catalog.json` and `agents/` together; a row whose file is missing is a
broken library, not a generic child. Keep `path` values relative to this
directory. Success-rate, speed and token figures inside the bodies are
unverified marketing claims from the source corpus — do not repeat them as
facts.
