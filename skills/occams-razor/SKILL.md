---
name: occams-razor
description: Apply the principle of parsimony to eliminate unneeded abstractions, third-party bloat, and speculative complexity.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, occams-razor, parsimony, simplicity, anti-bloat]
triggers:
  - "occam's razor"
  - "occams razor"
  - "simplify solution"
  - "minimum assumptions"
  - "cut unnecessary complexity"
---

# Occam's Razor Skill

## Overview
Based on William of Ockham's law of parsimony: *"Non sunt multiplicanda entia sine necessitate"* ("Entities should not be multiplied beyond necessity"). In software engineering and AI systems, choose the design that introduces the fewest moving parts, smallest dependency tree, and simplest mental model.

## Procedure
1. **Catalogue All Moving Parts**:
   - List every class, protocol, library import, environment variable, and configuration key in the proposed solution.
2. **Challenge Each Dependency**:
   - For every component, ask: *Does this solve a present, verified requirement, or a speculative future desire?*
   - Can this be accomplished in 15 lines of standard library code rather than adding a 50MB dependency?
3. **Prune Speculative Abstractions**:
   - Remove abstract base classes with only one implementation.
   - Eliminate indirection layers that merely pass arguments down unchanged.
   - Replace distributed architectures with local in-process primitives when traffic permits.
4. **Benchmark Simplicity**:
   - Measure LOC delta, bundle size delta, and startup latency before and after pruning.
