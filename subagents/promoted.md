# Import Manifest — all 607 subagents (supersedes P3's 16-agent manifest)

All 607 Claude Code agent definitions from the 610ClaudeSubagents corpus adapted
into invokable opencode subagents on 2026-10-10.

## What was done
- Phases: P0 inventory (catalog.json) → P1 taxonomy (7 categories) → P2 orchestration
  docs → P3 first 16 promoted → **P4b: all remaining 591 promoted**.
- Source: `C:\Users\<you>\610ClaudeSubagents\agents\<slug>.md`
- Target: `C:\Users\<you>\HarnessAgents\.opencode\agent\<slug>.md` (`mode: subagent`)
- Converter: `hive/library/subagents/p4_import_all.ps1` (working copy in
  `C:\Users\<you>\AppData\Local\Temp\opencode\p4_import_all.ps1`)
  - Frontmatter split tolerates both corpus styles: leading `---` YAML fences
    (603 agents) and the `## Agent Metadata` markdown style (4 agents, imported
    verbatim).
  - One source file used LF-only line endings; splitter normalizes `\r\n`/`\r`.
  - ASCII-only script; runs under PowerShell ExecutionPolicy Bypass.

## Permission archetypes (denied tools) — assigned per agent
P4b assigns an archetype to each of the 591 new agents from the tools each source
declares (the 16 P3 agents keep their hand-tuned maps unchanged):

- **research** (deny question, skill, lsp, doom_loop) — agent declares WebFetch /
  WebSearch: needs the web.
- **engineer** (deny webfetch, websearch, task, todowrite, skill, lsp, doom_loop,
  question) — agent declares Bash but not the web.
- **readonly** (deny bash, webfetch, websearch, task, todowrite, skill, lsp,
  doom_loop, question) — agent declares neither; analysis/writing judges.

Reused P3's exact deny-maps for the other archetypes (synth/docs) where relevant.
Rationale (also in PROTOCOL.md): least-privilege default-allow means anything not
explicitly denied stays available; this only closes dangerous escape hatches.

## Integrity note
Each output carries an HTML-comment provenance banner citing the source slug and
the adaptation date, and instructs the agent that success-rate / speed / token
figures in the source body are UNVERIFIED marketing claims. Bodies imported
verbatim; only frontmatter and the banner are new.

## Verification
- `opencode agent list` (v1.18.21) reports **609 subagents** = 607 imported +
  the 2 built-ins (`explore`, `general`). All 607 files parsed with no start
  failure, no "unknown agent" diagnostics; each slug appears exactly once.
- Every generated file has both a `description:` and a `permission:` block; none
  are near-empty (<500 B). 0 truncation errors.
- NOTE: opencode does not hot-reload; a restart is required for the new agents
  to be available inside live sessions.

## Known caveats / not-done
- ~40 near-duplicate test-* agents were NOT collapsed in the corpus; they are
  all imported as-is (P1 documented the duplicate set in `_duplicates.md`).
- `written-communication-enhancer` (references nonexistent tools
  writing_analysis/tone_detection) is imported; its unverifiable tool refs live
  in the body, not the frontmatter, so it parses and runs as a plain readonly
  writer.
- Tool field noise in the corpus (e.g. a stray `[WebSearch` token) is ignored by
  the archetype resolver via clean-token matching only.