# Cookbook: Test-Driven Development Loop

## Objective
Implement features using strict test-driven development: write failing test, write minimal passing code, refactor.

## Prerequisites
- python 3.11+
- pytest

## Execution Recipe
1. **Write Failing Test (Red)**:
   Create `tests/test_<feature>.py` asserting expected input/output behavior.
2. **Run Test to Verify Failure**:
   ```powershell
   python -m pytest tests/test_<feature>.py -q
   ```
3. **Implement Minimal Code (Green)**:
   Write the simplest implementation that satisfies the test assertion.
4. **Run Test to Verify Success**:
   ```powershell
   python -m pytest tests/test_<feature>.py -q
   ```
5. **Refactor & Verify**:
   Clean up code, run full test suite, and check linting:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
6. **Remediation**:
   If test passes without code changes, refine assertion to test boundary conditions.
