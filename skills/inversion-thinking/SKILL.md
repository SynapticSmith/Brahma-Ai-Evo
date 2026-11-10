---
name: inversion-thinking
description: Solve complex engineering problems backwards by identifying catastrophic failure modes and eliminating them systematically.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, inversion, problem-solving, failure-avoidance]
triggers:
  - "inversion thinking"
  - "invert always invert"
  - "solve backwards"
  - "how to guarantee failure"
  - "avoid anti-patterns"
---

# Inversion Thinking Skill

## Overview
Based on mathematician Carl Jacobi's principle *"Man muss immer umkehren"* ("Invert, always invert"). Instead of asking how to achieve success, ask how to guarantee failure, map out all anti-patterns and vulnerabilities that cause that failure, and systematically eliminate them.

## Procedure
1. **Define the Goal and its Inverse**:
   - State the target outcome clearly (e.g. "Zero-loss data persistence under crash conditions").
   - Define the anti-goal: "How can we guarantee data corruption, silent data loss, and crashed processes?"
2. **Enumerate Failure Drivers**:
   - List every practice, assumption, race condition, or architecture flaw that makes the anti-goal inevitable.
   - Example: Unbuffered writes, non-atomic file renames, ignoring SIGTERM, lack of disk sync.
3. **Invert Failure Drivers into Non-Negotiable Constraints**:
   - For every failure driver, construct an active guardrail or negative constraint.
   - Example: "Never write directly to destination; write to `.tmp` and atomic replace."
4. **Audit Proposed Solution Against Anti-Goals**:
   - Review current code and architecture against the inverted constraints.
   - Prune any design element that resembles a failure driver.
