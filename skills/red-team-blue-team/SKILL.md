---
name: red-team-blue-team
description: Dual-perspective adversarial simulation where Red probes for exploits and bypasses while Blue patches defenses and authors test gates.
metadata:
  domain: security
  tags: [security, adversarial, red-team, blue-team, stress-testing, penetration, audit]
triggers:
  - "red team blue team"
  - "adversarial simulation"
  - "red team audit"
  - "probe for exploits"
  - "stress test defenses"
  - "find bypasses"
---

# Red Team / Blue Team Adversarial Simulation Skill

## Overview
Drawn from military war-gaming and cybersecurity operations, this skill simulates adversarial dynamics within the coding harness:
- **Red Team (Adversary)**: Thinks like an attacker. Seeks path traversals, permission bypasses, prompt injection breakouts, resource exhaustion (fork-bombs, memory leaks), credential leaks, and covert channels.
- **Blue Team (Defender)**: Thinks like a defensive engineer. Implements containment boundaries, input validation filters, cryptographic verification, AST scanners, and regression test suites.

## Simulation Protocol
1. **Red Team Phase (Offense)**:
   - Identify system boundaries (network egress, file system writes, child processes, deserialization).
   - Author a test case attempting to break or bypass the boundary (e.g. attempting to read outside the workspace, forging an unsigned release manifest).
2. **Blue Team Phase (Defense)**:
   - Analyze how the bypass succeeded.
   - Implement a defensive guardrail (e.g. `path.resolve().is_relative_to(root)`).
   - Ensure containment bounds lifetime and execution scope.
3. **Synthesis & Test Consolidation**:
   - Incorporate the adversarial test into the automated CI test suite (e.g. `tests/test_redteam.py`).
