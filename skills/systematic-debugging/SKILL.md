---
name: systematic-debugging
description: 4-phase deterministic debugging protocol to reproduce, isolate, fix, and verify software defects without collateral damage.
metadata:
  domain: coding
  tags: [debugging, root-cause, reproduction, testing, hypothesis, verification]
triggers:
  - "systematic debugging"
  - "debug this bug"
  - "reproduce bug"
  - "root cause of failure"
  - "isolate bug"
  - "fix this failure"
---

# Systematic Debugging Skill

## Overview
Drawn from the `superpowers` framework, this skill replaces trial-and-error patching with a disciplined scientific method:
1. **Phase 1: Deterministic Reproduction**: Never edit code until the defect is reliably reproduced by an automated test case or minimal script.
2. **Phase 2: Root-Cause Isolation**: Formulate falsifiable causal hypotheses; trace variable state and stack frames back to the origin of the corruption.
3. **Phase 3: Surgical Remediation**: Implement the minimal necessary change that restores invariants without side effects.
4. **Phase 4: Regression Shielding**: Turn the reproduction case into a permanent test in the regression suite.

## Core Rules
- **No Speculative Edits**: If you cannot explain *why* a change fixes the issue, do not apply it.
- **Isolate One Variable at a Time**: Change exactly one parameter or line when testing a hypothesis.
- **Preserve Clean Diffs**: Revert exploratory debug logging or instrumentation before submitting the fix.
