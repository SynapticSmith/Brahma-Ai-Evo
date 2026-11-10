# Cookbook: Systemic Diagnostic Attribution via Hanlon's Razor

## Objective
Diagnose unexpected errors and security rejections by systematically auditing benign operational causes before escalating to complex adversarial hypotheses.

## Prerequisites
- Error report, connection failure, or unexpected system denial.

## Execution Recipe
1. **Audit Input Types and Serialization**:
   Inspect the raw payloads crossing the boundary:
   - Check for UTF-8 BOM encoding issues (`utf-8` vs `utf-8-sig`).
   - Check for string vs integer type mismatches in JSON arguments.
2. **Inspect Environmental Context**:
   - Verify file permissions, current working directory (`cwd`), and path existence.
   - Verify environment variable availability (`echo $env:VARIABLE`).
3. **Inspect Network / Socket Timing**:
   - Check if the remote host closed the connection due to an unhandled exception or keepalive timeout.
   - Review server-side stderr/logs for 400 Bad Request / 1007 Invalid Argument details.
4. **Inspect State Concurrency**:
   - Check for simultaneous reads and writes to non-thread-safe data structures.
5. **Evaluate Adversarial Breach Only If Benign Causes Are Eliminated**:
   If inputs, config, and concurrency are provably valid, initiate red-team security review.
6. **Verification Gate**:
   Reproduce the failure with a benign minimal test case and verify the fix removes the anomaly.
7. **Remediation**:
   Add input validation and descriptive error logging so future misconfigurations do not masquerade as mysterious failures.
