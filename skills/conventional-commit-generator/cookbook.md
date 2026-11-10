# Cookbook: Atomic Conventional Commit Generation

## Objective
Inspect git diffs, infer type and scope, and author a clean Conventional Commit message.

## Prerequisites
- Working changes present in git workspace.

## Execution Recipe
1. **Inspect Staged Diffs**:
   ```powershell
   git status --short
   git diff --staged --stat
   ```
   If nothing is staged, stage specific files: `git add <files>`.
2. **Infer Scope & Type**:
   - Determine folder / module (e.g. `agent`, `skills`, `core`, `tests`).
   - Match action: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`.
3. **Draft Subject Line**:
   `<type>(<scope>): <concise imperative summary under 72 chars>`
4. **Commit with Optional Why-Body**:
   ```powershell
   git commit -m "<type>(<scope>): <subject>" -m "<Why this change was made and impact on consumers>"
   ```
5. **Verification Gate**:
   ```powershell
   git log -1 --stat
   ```
   *Gate: Commit hash created, message adheres to `<type>(<scope>): <subject>` format.*
6. **Remediation**:
   If an error or typo is present in the latest un-pushed commit:
   `git commit --amend` to modify message or staged files.
