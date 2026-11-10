# Cookbook: Inversion-Based Problem Solving

## Objective
Apply inversion thinking to discover hidden flaws, anti-patterns, and fatal failure paths in an architecture or codebase before implementation.

## Prerequisites
- A clear technical objective or architectural decision.

## Execution Recipe
1. **Identify Success Goal**:
   Formulate the desired state in one concise sentence (e.g., "Harness runs untrusted code safely without resource exhaustion").
2. **Construct Anti-Goal**:
   Formulate the opposite condition (e.g., "Harness allows runaway fork-bombs, memory leaks, and disk exhaustion").
3. **Brainstorm Failure Mechanisms**:
   List at least 4 ways to cause the anti-goal:
   - Unbounded memory allocations
   - Subprocesses not attached to job objects
   - Infinite recursive loops without turn limits
   - File descriptors left unclosed
4. **Translate to Active Invariants**:
   Convert each failure mechanism into an automated test or architectural gate:
   - Cap subprocess memory with Job Object limits.
   - Enforce hard turn budgets (max 12 iterations).
   - Use `with` blocks or explicit try-finally cleanup for all I/O handles.
5. **Verification Gate**:
   Execute test suite to prove each anti-goal scenario is blocked and raises expected exceptions.
6. **Remediation**:
   If an anti-goal scenario slips through, insert an AST check or policy rule to reject it before runtime.
