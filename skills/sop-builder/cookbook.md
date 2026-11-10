# Cookbook: Compiling an Operational SOP / Runbook

## Objective
Author a production-ready Standard Operating Procedure (SOP) that any agent or on-call engineer can execute without ambiguity.

## Prerequisites
- A verified, repeatable workflow (e.g. database migration, release build, certificate rotation).

## Execution Recipe
1. **Define Objective and Boundaries**:
   State what the SOP accomplishes in one sentence and specify non-applicable contexts.
2. **List Required Tooling and Environment**:
   List exact CLI utilities, Python versions, and authentication tokens required before starting.
3. **Format Step Sequence**:
   For every action step, provide:
   - **Command**: Copy-paste ready shell command.
   - **Expected Output**: Exact regex or string indicating success.
   - **Failure Signal**: Error text that triggers immediate abort.
4. **Author the Rollback Section**:
   Write the exact inverse sequence of commands to execute if a failure signal appears:
   ```powershell
   # Emergency Rollback Sequence
   git restore .
   python tools/restore_backup.py --snapshot pre_migration
   ```
5. **Verification Gate**:
   Perform a clean test run of the SOP in a staging or dry-run environment.
6. **Remediation**:
   If an operator gets stuck at any step, revise the step description to include prerequisite installation or environment setup instructions.
