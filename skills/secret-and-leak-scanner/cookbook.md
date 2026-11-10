# Cookbook: Pre-Commit Secret & Credential Leak Audit

## Objective
Scan repository files and staged changes for leaked credentials, private keys, and high-entropy API tokens.

## Prerequisites
- Git repository workspace.
- PowerShell or bash terminal.

## Execution Recipe
1. **Scan Untracked & Sensitive Files**:
   ```powershell
   Get-ChildItem -Recurse -Include *.env, *.pem, *.key, *id_rsa*, credentials.json -Exclude .git, node_modules, .venv
   ```
2. **Audit Staged Diffs for Token Signatures**:
   ```powershell
   $patterns = @('sk-[a-zA-Z0-9]{20,}', 'ghp_[a-zA-Z0-9]{36}', 'AKIA[0-9A-Z]{16}', 'bearer [a-zA-Z0-9_\-\.]{25,}')
   git diff --staged | Select-String -Pattern $patterns
   ```
3. **Scan Recent Commits for Secret Ingestion**:
   ```powershell
   git log -p -n 5 | Select-String -Pattern $patterns
   ```
4. **Verification Gate**:
   *Gate: Zero matches found across file names, staged diffs, and commit logs.*
5. **Remediation**:
   - If uncommitted: add file to `.gitignore`, run `git reset <file>`.
   - If committed in git: purge via `git filter-repo --invert-paths --path <file>`, force rotate the credential immediately at the third-party provider.
