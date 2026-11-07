---
type: system-entry
---
# Subagent Orchestration — entry & routing

Where am I: the Subagent Orchestration system ported from
`HarnessAgents` (the 610ClaudeSubagents integration, imported 2026-10-10).
This is the machine-wide reference: any agent or harness can dispatch against
this catalog. This file routes — it holds no agent bodies.

## What is here

| Item | What it is | Entry / file |
|---|---|---|
| Catalog (610 rows) | Machine-readable routing index: name, slug, category, one-line description, tools, source path | `catalog.json` |
| Taxonomy | 7 categories → agents (human-readable) | `taxonomy.md` |
| Promoted pairs | 16 hand-tuned lead agents saved before the bulk import | `promoted.md` |
| Duplicates note | Known near-duplicates across the corpus | `duplicates.md` |
| Agent bodies | 607 opencode-format subagent definitions (one file each) | `agents/` |
| Orchestration patterns | Multi-agent flow patterns (fan-out, DAG, convergence, verification) | `orchestration/patterns.md` |
| Agent authoring | How agent definitions are written per this system | `orchestration/authoring.md` |
| Orchestration index | How the 3 orchestration docs fit together | `orchestration/index.md` |
| Import script | The bulk converter (reproducible, provenance) | `convert-import-script.ps1` |

## Routing rules
- **Never sweep `agents/` to choose an agent.** Read `catalog.json` (or
  `taxonomy.md` for the category view) and judge fit from the one-line
  description.
- Only when several candidates look close, read the specific agent body via its
  catalog `path`.
- Dispatch by the catalog `slug` (e.g. `subagent_type = "ml-model"`).
- Treated conservatively: success-rate / speed / token figures inside agent
  bodies are UNVERIFIED marketing claims — verify before asserting.
- This directory is a **port**, not the canonical copy. Canonical sources:
  - Corpus: `C:\Users\<you>\610ClaudeSubagents\agents\`
  - Imported build: `C:\Users\<you>\HarnessAgents\.opencode\agent\`
  - Catalog of record: `C:\Users\<you>\HarnessAgents\hive\library\subagents\catalog.json`

## Protocol
Dispatch-level policy lives in `HarnessAgents/hive/PROTOCOL.md`
("Routing: catalog → dispatch"). The shared hive (god, jim) coordinates through
that protocol, not this vault.