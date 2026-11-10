---
name: first-principles-thinking
description: Deconstruct complex problems into fundamental axioms and reassemble solutions from the ground up without relying on analogies.
metadata:
  domain: formal-sciences/logic
  tags: [thinking, reasoning, first-principles, logic, problem-solving]
triggers:
  - "first principles"
  - "break this problem down"
  - "deconstruct the problem"
  - "fundamental truths"
---

# First-Principles Thinking Skill

## Overview
Deconstruct any engineering, architectural, or algorithmic problem down to its most basic, undeniable truths, bypassing superficial conventions and analogies to construct novel, optimal solutions.

## Procedure
1. **Clarify Thinking & Identify Assumptions**:
   - Write down the problem statement explicitly.
   - List every assumption currently being taken for granted (e.g., "we need an AGI model to do this", "we need a giant vector database", "this requires cloud infrastructure").
2. **Deconstruct to Fundamental Realities**:
   - For each assumption, ask: *Is this fundamentally, physically, or mathematically true, or is it an inherited convention?*
   - Strip away layer by layer until reaching ground truths (e.g., memory limits, CPU latency, filesystem I/O, deterministic parsing).
3. **Reconstruct from Ground Truth**:
   - Rebuild the solution solely from foundational truths.
   - Combine simple, proven primitives (e.g., deterministic regex + BM25 + filesystem hierarchy) rather than adopting heavy external dependencies.
4. **Stress-Test the Synthesis**:
   - Verify edge cases, boundary conditions, and worst-case algorithmic complexity.
   - Confirm the reconstructed model achieves the desired outcome with minimum conceptual overhead.
