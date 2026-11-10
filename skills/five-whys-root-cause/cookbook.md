# Cookbook: 5-Whys Root-Cause Investigation

## Objective
Systematically diagnose a failing test, bug, or production defect by chaining interrogations down to the foundational failure mechanism.

## Prerequisites
- A reproducible failure trace, error log, or failing unit test.

## Execution Recipe
1. **Capture the Ground Failure**:
   Run the failing command and record the full traceback:
   ```powershell
   python -m pytest tests/test_failing.py -q -vv
   ```
2. **Execute the 5 Whys Chain**:
   Write down the sequence:
   - *Why 1 (Symptom)*: What direct line threw the exception?
   - *Why 2 (State)*: What unexpected value or state triggered that line?
   - *Why 3 (Mechanism)*: Where did that bad value originate?
   - *Why 4 (Contract)*: Why did the preceding boundary/function fail to validate or sanitize it?
   - *Why 5 (Architecture/Policy)*: Why was there no schema, lint rule, or test asserting this invariant?
3. **Formulate the Structural Fix**:
   Write code addressing the root invariant at Why #4 or #5 (e.g., input validation at boundary, synchronization gate, state machine transition check).
4. **Author Regression Test**:
   Create a dedicated unit test reproducing the exact sequence that triggered the failure, proving the root cause is eliminated.
5. **Verification Gate**:
   Run the full test suite and verify the bug is resolved without breaking existing invariants:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
6. **Remediation**:
   If the bug recurs under high concurrency, inspect thread safety and lock contention across shared state.
