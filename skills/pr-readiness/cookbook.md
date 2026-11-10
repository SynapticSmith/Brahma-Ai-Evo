# Cookbook: Branch PR Readiness & Pre-Flight Validation

## Objective
Validate that a branch is synced, tested, linted, and ready for immediate pull request creation without CI failures.

## Prerequisites
- Feature or bugfix branch checked out (not `main`).
- GitHub CLI (`gh`) or git remote configured.

## Execution Recipe
1. **Sync Check with Target**:
   ```powershell
   git fetch origin main
   $behind = git rev-list --count HEAD..origin/main
   if ($behind -gt 0) { git rebase origin/main }
   ```
2. **Execute Local Test Suite**:
   ```powershell
   py -3.11 -m pytest tests -q
   ```
3. **Run Linter & Static Checks**:
   ```powershell
   py -3.11 -m ruff check .
   ```
4. **Inspect Diff & Generate PR Description**:
   ```powershell
   git log origin/main..HEAD --oneline
   ```
   Draft markdown description:
   - **Summary**: Concise high-level intent.
   - **Changes**: Bulleted breakdown of commits.
   - **Verification**: Exact test commands executed.
5. **Submit PR**:
   ```powershell
   gh pr create --title "<type>(<scope>): <subject>" --body-file pr_body.md
   ```
6. **Verification Gate**:
   *Gate: Tests pass 100%, linter returns 0 errors, no merge conflicts.*
7. **Remediation**:
   If behind target branch or tests fail, resolve conflicts locally, rerun tests, and re-verify before submitting.
