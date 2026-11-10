# Cookbook: Systematic Bug Isolation and Repair

## Objective
Reproduce an unexpected error or test failure, isolate the root cause, and apply a surgical fix guarded by a permanent test.

## Prerequisites
- Working dev environment (`python -m pytest` available).
- Reproducible error message, traceback, or unexpected behavior.

## Execution Recipe
1. **Capture the Traceback and Failing Inputs**:
   Identify the entry point, the failing function, the exact exception type, and the argument values.
2. **Author a Minimal Failing Test**:
   Create a reproduction test in `tests/test_<subsystem>.py`:
   ```python
   def test_reproduce_reported_bug():
       # Provide the exact input that triggered the defect
       result = function_under_test(trigger_input)
       assert result == expected_output
   ```
3. **Run the Reproduction Test**:
   ```powershell
   python -m pytest tests/test_<subsystem>.py -k "test_reproduce" -v
   ```
   *Gate: The test must fail with the reported error.*
4. **Inspect State Invariants**:
   Check if the defect arises from:
   - Type mismatch or unexpected `None`/null value
   - Off-by-one boundary condition in loop or slice
   - Unhandled edge case in conditional branch
   - Asynchronous race condition or missing lock
5. **Apply Minimal Surgical Fix**:
   Edit only the source lines responsible for the corrupted invariant.
6. **Verification Gate**:
   Rerun reproduction test and full subsystem suite:
   ```powershell
   python -m pytest tests/test_<subsystem>.py -v
   python -m ruff check .
   ```
7. **Remediation**:
   If other tests break, the fix introduced collateral damage. Revert the edit and re-evaluate the hypothesis.
