# Cookbook: Pre-Mortem Risk Immunization

## Objective
Identify and neutralize high-risk failure modes before initiating breaking changes or production operations.

## Prerequisites
- A planned migration, refactoring, or infrastructure update.

## Execution Recipe
1. **Declare Hypothetical Catastrophe**:
   Document the exact failure state: "The migration script ran, wiped 30% of user data, and the rollback failed."
2. **Scan for Failure Vectors**:
   Inspect the code and operational plan for:
   - Destructive commands without dry-run flags (`DROP TABLE`, `rm -rf`, `git reset --hard`)
   - Unhandled exception paths inside loops
   - Missing atomic transaction wrappers
3. **Draft the Emergency Rollback Recipe**:
   Write the exact CLI command sequence required to restore state within 60 seconds if failure strikes.
4. **Implement Defense Fences**:
   - Add SQLite backup snapshot step before running schema changes.
   - Enforce dry-run mode by default; require `--force` or explicit confirmation for destructive actions.
5. **Verification Gate**:
   Simulate an intentional mid-execution abort (e.g. `KeyboardInterrupt`) and verify the database or workspace rolls back cleanly without corruption.
6. **Remediation**:
   If an operation cannot be cleanly rolled back, refactor it into an append-only or versioned shadow table strategy.
