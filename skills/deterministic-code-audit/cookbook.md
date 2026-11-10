# Cookbook: Pre-Commit Security & Invariant Audit

## Objective
Scan repository diffs or target source files for critical security vulnerabilities, Null Pointer dereferences, and resource leaks.

## Prerequisites
- Git diff or target source files to audit.
- Python 3.11+ environment with `ruff` or static linter.

## Execution Recipe
1. **Inspect Staged Changes**:
   ```powershell
   git diff --staged
   ```
2. **Scan for Injection Patterns**:
   Search for raw shell commands, unsafe SQL concatenation, and path traversals:
   - Verify all `subprocess` invocations use array arguments with `shell=False`.
   - Verify all SQL queries use parameterized placeholders (`?` or `:name`).
   - Verify all path resolution checks `path.resolve().is_relative_to(root)`.
3. **Scan for Null / None Dereferences**:
   Check functions returning `Optional[...]` to ensure callers handle `None` prior to attribute access.
4. **Scan for Resource Leaks**:
   Verify all file streams, sockets, and subprocess trees use context managers or cleanup handlers:
   - Ensure child processes are terminated in `finally` blocks or bound to Job Objects.
5. **Run Static Analyzer**:
   ```powershell
   python -m ruff check .
   ```
6. **Verification Gate**:
   - 0 raw shell commands with user variables.
   - 0 unparameterized SQL queries.
   - 0 linter violations.
7. **Remediation**:
   Replace any dangerous pattern with a safe standard-library primitive before staging commits.
