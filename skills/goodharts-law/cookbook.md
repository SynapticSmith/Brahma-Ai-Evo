# Cookbook: Auditing Metric Ratchets Against Goodhart's Pathology

## Objective
Evaluate a software target, architectural ratchet, or benchmark against Goodhart's Law to prevent unintended degeneration or metric gaming.

## Prerequisites
- A quantitative goal (e.g. line count ceiling, test count target, latency threshold).

## Execution Recipe
1. **Identify the Target Metric**:
   State the exact threshold (e.g., `core/tool_exec.py <= 147 lines and 0 elif branches`).
2. **Perform Adversarial Gaming Simulation**:
   Brainstorm 3 ways to meet the metric while degrading the system:
   - *Gaming 1*: Shoving complex logic into dictionary lookups or `exec()` strings to avoid `elif`.
   - *Gaming 2*: Removing comments and docstrings to stay under the line count ceiling.
   - *Gaming 3*: Splitting code into arbitrary tiny files that obscure the execution flow.
3. **Establish Counter-Balancing Invariants**:
   For each gaming vector, establish a protective constraint:
   - Add AST check prohibiting raw `exec`/`eval`.
   - Mandate docstring preservation rule (`AGENTS.md`).
   - Require architectural cohesion in new module extraction.
4. **Verification Gate**:
   Verify the metric is achieved through substantive architectural refactoring rather than cosmetic compression.
5. **Remediation**:
   If metric pressure forces bad code patterns, adjust the metric ceiling to reflect clean architectural boundaries.
