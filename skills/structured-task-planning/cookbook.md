# Cookbook: Atomic Task Breakdown and Dependency Ordering

## Objective
Convert a feature request or major refactor into a sequence of isolated, verifiable tasks with zero merge conflicts.

## Prerequisites
- High-level user requirement or feature proposal.
- Knowledge of target repository directory layout.

## Execution Recipe
1. **Catalog Affected Files and Seams**:
   List all modules, classes, and test suites that will be created or modified.
2. **Order by Dependency Layer**:
   Arrange execution into strict topological tiers:
   - Tier 1: Data schemas, type definitions, and database migrations.
   - Tier 2: Core domain logic and business functions.
   - Tier 3: Harness tools, adapters, and API endpoints.
   - Tier 4: User interfaces, CLI commands, and telemetry.
3. **Draft the Implementation Checklist**:
   Format each subtask as:
   - **Task ID & Name**: e.g., `Task 1: Add UserSession dataclass`.
   - **Files to Edit**: Explicit relative paths.
   - **Acceptance Criteria**: Concrete condition (e.g., `assert session.is_valid()`).
   - **Verification Command**: Exact CLI test command.
4. **Execution Protocol**:
   Execute tasks one at a time. Run tests immediately after each task before proceeding to the next.
5. **Verification Gate**:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
6. **Remediation**:
   If a task is blocked by unexpected legacy coupling, halt, extract the blocker into an isolated preparatory task, and re-order the queue.
