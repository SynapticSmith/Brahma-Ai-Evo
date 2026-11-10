# Cookbook: Automated Code Review

## Objective
Perform a structured, line-by-line architectural and syntax review on git diffs before merging.

## Prerequisites
- git
- python 3.11+

## Execution Recipe
1. **Inspect Staged Diffs**:
   ```powershell
   git diff --staged
   ```
2. **Run Linter & Static Analysis**:
   ```powershell
   python -m ruff check .
   ```
3. **Execute Targeted Regression Tests**:
   ```powershell
   python -m pytest tests -q
   ```
4. **Verification Gate**:
   Ensure 0 lint errors, 0 test failures, and no unprotected secret keys or temporary debug statements.
5. **Remediation**:
   Use `ruff check --fix .` for automated fixes; revert manual regressions if tests fail.
