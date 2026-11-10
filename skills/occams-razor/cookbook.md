# Cookbook: Complexity Pruning via Occam's Razor

## Objective
Strip speculative complexity and excess dependencies from a code module, leaving only minimal, verified primitives.

## Prerequisites
- A working test suite covering the target module's functional requirements.

## Execution Recipe
1. **Audit External Imports**:
   Scan target files for heavy third-party dependencies:
   ```powershell
   Select-String -Path ".\*.py" -Pattern "^import |^from " | Group-Object Line
   ```
2. **Evaluate In-House Substitution**:
   Identify libraries imported for trivial utilities (e.g., `requests` for a single GET request, `lodash` for cloning). Replace with Python stdlib (`urllib.request`, `copy.deepcopy`).
3. **Flatten Class Hierarchies**:
   Collapse multi-level class inheritances into simple data classes or pure functions when no polymorphic behavior is needed.
4. **Remove Unused Knobs**:
   Delete configuration parameters that have never been overridden in production or tests.
5. **Verification Gate**:
   Run unit tests and lint checks:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
   All tests must pass with zero behavioral regressions and fewer dependencies.
6. **Remediation**:
   If an edge case required a deleted abstraction, restore only the minimum inline logic needed to handle that specific edge case.
