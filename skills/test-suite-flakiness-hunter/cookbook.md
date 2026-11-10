# Cookbook: Flaky Test Stress Reproduction & Deterministic Repair

## Objective
Reproduce an intermittent test failure, isolate the root cause, and replace arbitrary sleeps with deterministic condition polling.

## Prerequisites
- Pytest environment (`pytest`).

## Execution Recipe
1. **Stress-Test Reproduction Loop**:
   Run target test 50 times in rapid succession to verify failure rate:
   ```powershell
   1..50 | ForEach-Object { py -3.11 -m pytest tests/test_target.py -k "test_flaky_case" -q } | Select-String "FAILED"
   ```
2. **Replace `time.sleep()` with Condition Polling**:
   Refactor hardcoded sleep to a deterministic predicate loop:
   ```python
   import time

   def wait_until(predicate, timeout=5.0, interval=0.05):
       deadline = time.monotonic() + timeout
       while time.monotonic() < deadline:
           if predicate():
               return True
           time.sleep(interval)
       raise TimeoutError("Condition not met within timeout")
   ```
3. **Verify Random Order Execution**:
   Run tests with shuffled ordering to ensure zero cross-test global state contamination:
   ```powershell
   py -3.11 -m pytest tests/test_target.py --random-order
   ```
4. **Verification Gate**:
   *Gate: Test suite passes 100 consecutive runs with 0 failures.*
5. **Remediation**:
   If test still fails intermittently, check unclosed background threads or lingering file locks.
