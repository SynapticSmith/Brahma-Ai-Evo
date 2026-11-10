# Cookbook: Investigating and Retiring Legacy Fences Safely

## Objective
Safely evaluate, verify, and either preserve or retire legacy code, constraints, or workarounds without re-introducing historical bugs.

## Prerequisites
- A target code block, workaround, or condition proposed for deletion.
- Access to git history (`git log`, `git blame`).

## Execution Recipe
1. **Trace Git Provenance**:
   Run `git log -S` or `git blame` on the lines in question:
   ```powershell
   git log -L <start>,<end>:<file>
   ```
   Read the commit message, author notes, and linked PR/issue.
2. **Formulate the Original Threat Hypothesis**:
   Answer in writing:
   - What broke when this fence was absent?
   - What operating system, library version, or external API behavior mandated it?
3. **Check Current Environmental Reality**:
   Determine whether the upstream bug was resolved or whether the platform constraint remains active.
4. **Author a Boundary Test Case**:
   Before modifying the code, write a unit test reproducing the exact failure mode the fence protects against.
5. **Evaluate Safe Removal or Simplification**:
   - If the condition is still active: document the rationale with an explicit comment and retain the fence.
   - If the condition is obsolete: run the test suite with the fence removed.
6. **Verification Gate**:
   ```powershell
   python -m pytest tests -q
   ```
7. **Remediation**:
   If an unexpected test fails or regressions occur, revert immediately: the fence is still protecting the system.
