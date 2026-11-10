# Cookbook: Adversarial Red/Blue Security Verification

## Objective
Proactively probe a newly implemented tool, API endpoint, or permission boundary for security bypasses and harden it with automated tests.

## Prerequisites
- A target module, tool specification, or permission check.

## Execution Recipe
1. **Red Team Probe Phase**:
   Draft an exploit test case in `tests/test_redteam.py` attempting to violate system invariants:
   - Path Traversal: Passing `../../etc/passwd` or `..\..\Windows\System32`.
   - Command Injection: Passing `; rm -rf /` or `& calc.exe`.
   - Resource Exhaustion: Triggering an infinite recursive call or huge allocation.
2. **Execute Probe**:
   ```powershell
   python -m pytest tests/test_redteam.py -k "test_adversarial_probe" -v
   ```
   *Observation: Did the defense block the attack, or did the breach succeed?*
3. **Blue Team Defense Phase**:
   If the probe succeeded, implement immediate hardening:
   - Enforce path normalization via `Path.resolve()` and verify containment.
   - Restrict tool command execution to stdlib `run_captured` without `shell=True`.
   - Enforce memory and execution timeouts via Job Objects.
4. **Permanent Test Integration**:
   Rename the probe into a permanent regression test asserting that the exploit raises a permission denial or sanitized result.
5. **Verification Gate**:
   ```powershell
   python -m pytest tests/test_redteam.py -v
   python -m ruff check .
   ```
6. **Remediation**:
   If an exploit vector cannot be closed in software alone, add a policy-level `deny` rule in `agent_permissions.json`.
