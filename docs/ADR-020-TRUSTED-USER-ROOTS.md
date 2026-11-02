# ADR-020 — Trusted user roots and trusted workspaces

**Status:** Accepted (ratified 2026-11-01) · **Gates:** O-1 input-trust item (B8), and the
prompt-fatigue part of the O-6 migration · **Supersedes:** nothing
**Related:** `agent/permissions.py` (path scoping), `agent/prompts.py` (instruction
discovery), `ARCHITECTURE-SPEC.md` §7.3 item 8, §12 Q1.5, §13 Q4

## Context

Two different "trust" questions are currently conflated, and neither is answered
in code:

1. **Path trust.** `agent/permissions.py:321–357` resolves each path argument and
   compares it to the workspace. Outside it, `paths.outside_workspace` decides
   (default `ask`); inside it, `protected_globs` still apply (default `ask`).
   A user who keeps projects in `D:\code` and notes in `Documents` is asked on
   every single file operation there. Prompt fatigue is the failure mode that
   makes an approval system get clicked through, so "ask everything forever" is
   not actually the safe end state.
2. **Input trust.** A cloned repository can ship `.mcp.json` (which spawns
   programs) and instruction files (`AGENTS.md`, `BRAHMA.md`, `.brahma/`). The
   harness reads instructions from the workspace and up to three parents
   (`agent/prompts.py`) and boots workspace MCP servers on the first turn. That
   is correct for a repo you chose and wrong for one you `git clone`d to look at.

The policy file is user data (`agent_permissions.json`, never committed). It must
therefore never be readable from the workspace it governs, and a workspace must
never be able to grant itself trust.

## Decision

### 1. `paths.trusted_roots` — an explicit, user-authored allowlist

```json
"paths": {
  "outside_workspace": "ask",
  "protected": "ask",
  "trusted_roots": ["D:\\code", "C:\\Users\\<name>\\Documents"]
}
```

- A path inside a trusted root takes the **`allow`** decision for
  `paths.outside_workspace` only. `paths.protected` and `protected_globs` still
  apply, evaluated against the path relative to the matched root. Trusting
  `D:\code` does not expose `D:\code\anything\.env`.
- Roots are absolute, normalized, and matched case-insensitively on Windows,
  after `Path.resolve()` (symlinks followed). A root matches its **descendants
  only**: `D:\code` does not match `D:\code2`, and there is no upward
  inheritance — a workspace nested under a trusted root does not make the parent
  trusted.
- **Trust requires an approver.** `headless.outside_workspace` is untouched by
  `trusted_roots`; a headless run keeps its own deny/ask rule. A grant made by a
  human applies when a human is present.
- The policy file is read from user data, never from the workspace, and it is
  re-read at the existing 1 s interval, so revocation is immediate.

### 2. `trusted_workspaces` — a store that gates *execution from a clone*

- A JSON store in the user-data directory (`core/user_paths.get_user_data_dir()`),
  one row per workspace root: normalized path, first-seen timestamp, and the
  policy version.
- **Gated:** repo-local MCP configuration (`.mcp.json` / workspace-declared
  servers) is **not** loaded for a workspace absent from the store. The first
  turn in that workspace reports "this workspace wants to start N MCP servers"
  and offers a trust action. This is the only path where a cloned repo can cause
  a program to run.
- **Not gated, but delimited:** instruction files (`AGENTS.md`, `BRAHMA.md`,
  `.brahma/AGENTS.md`) are still read in an untrusted workspace — refusing them
  would break the harness in every fresh clone — but their contents are wrapped
  in an explicit untrusted-content delimiter and do not gain approval authority.
  Trust gates execution, not reading.
- The fingerprint is the normalized path. **Not** the git remote: a remote is
  attacker-controlled text, and a repo moved to a different path is a different
  trust decision.
- Deleting a row revokes on the next turn; no restart.

### 3. No implicit grants

- A trusted root is never added by the model, by an action, or by a tool. The
  only writers are the user (policy edit) and the trust prompt (store row).
- Neither list is harvested from the workspace, an environment variable set by
  the workspace, or a project file.
- A root that fails resolution (missing drive, unreadable) is ignored with a
  diagnostic rather than treated as matched.

## Consequences

- The common case ("my own projects directory") stops prompting, while secrets
  inside it stay protected. Prompt fatigue goes down without widening what the
  model can touch silently.
- The approval surface keeps one vocabulary: this extends `paths`, it does not
  add a parallel allowlist (the N7 failure mode).
- A first-run in a new clone shows one extra prompt before any MCP server
  starts. That is the point.
- `agent/prompts.py` gains a delimiter wrapper; `core/user_paths.py` gains the
  store; `agent/permissions.py` gains one branch before the `relative_to`
  failure. No second policy engine.

## Open questions (for ratification)

1. Should `trusted_roots` be per-root scoped to read/write (`{"path": ...,
   "write": false}`) or uniform? *(Recommendation: uniform for v1; a read-only
   variant is a later amendment if users ask.)*
2. Should the trust prompt remember "this workspace, this session only" as well
   as permanently? *(Recommendation: no. Session-scoped trust already exists for
   approvals in-process; adding a third lifetime makes the mental model worse.)*
3. Is path-only fingerprinting of `trusted_workspaces` enough, given the store
   is user data and the threat is a clone, not a local attacker?
   *(Recommendation: yes for v1.)*

## Acceptance criteria for the implementing batch

- A path under a trusted root is allowed with no prompt; `D:\code2` is **not**
  matched by `D:\code`; a symlink that resolves outside the root is not trusted.
- `.env` / `*.key` under a trusted root still triggers `paths.protected`.
- Headless mode still denies outside the workspace regardless of
  `trusted_roots`.
- An untrusted workspace's `.mcp.json` is not loaded; trusting it loads it on the
  next turn without a restart.
- Instruction files are still read in an untrusted workspace and are visibly
  delimited in the prompt.
