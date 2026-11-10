# Cookbook: Git Branching & Atomic Commits

## Objective
Manage git branches, worktrees, and atomic commits safely without dirtying working trees.

## Prerequisites
- git

## Execution Recipe
1. **Check Status & Branch Cleanliness**:
   ```powershell
   git status
   ```
2. **Create Feature Branch or Worktree**:
   ```powershell
   git checkout -b feature/<task-name>
   ```
3. **Stage Specific Files & Commit**:
   ```powershell
   git add <path/to/file>
   git commit -m "type(scope): clear description of change"
   ```
4. **Verification Gate**:
   ```powershell
   git log -n 1 --stat
   ```
5. **Remediation**:
   If incorrect files are staged, unstage with `git restore --staged <file>`.
