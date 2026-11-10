# Cookbook: 3-Point Triangulation of Technical Claims

## Objective
Rigorously verify an architectural assumption, root-cause diagnosis, or third-party claim using three independent verification channels.

## Prerequisites
- A critical claim, bug diagnosis, or platform assumption requiring certainty.

## Execution Recipe
1. **Define the Hypothesis Under Test**:
   State the exact claim: e.g., "Windows Store Python 3.12 triggers Access Denied when assigning child processes to Job Objects."
2. **Channel 1 — Static Code & AST Inspection**:
   Examine source implementation: inspect `agent/process.py` and confirm `AssignProcessToJobObject` calls.
3. **Channel 2 — Empirical Runtime Execution**:
   Execute isolated reproduction test and capture exit code:
   ```powershell
   python -c "import ctypes; # simulate assign process to job object"
   ```
   Confirm exact error code (`ERROR_ACCESS_DENIED = 5`).
4. **Channel 3 — External Documentation / System Behavior**:
   Cross-reference Microsoft documentation on AppX/MSIX package execution aliases and job object security boundaries.
5. **Compile Triangulated Proof**:
   Synthesize all three channels into a conclusive finding with code references.
6. **Verification Gate**:
   Do all 3 channels agree without contradictory evidence?
7. **Remediation**:
   If channel 1 and channel 2 disagree (e.g. code says one thing, runtime shows another), investigate environment variables or dynamic imports causing divergence.
