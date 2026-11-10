---
name: blast-radius-analyzer
description: Structural code impact and blast-radius analyzer tracing call graphs, caller hierarchies, interface dependencies, and downstream risks prior to breaking changes.
metadata:
  domain: architecture
  tags: [blast-radius, call-graph, static-analysis, impact-analysis, dependencies, refactoring, architecture]
triggers:
  - "blast radius analyzer"
  - "calculate blast radius"
  - "change impact analysis"
  - "caller graph impact"
  - "what would break"
  - "downstream impact"
  - "assess blast radius"
---

# Blast Radius & Change Impact Analyzer Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s `code-review-graph` MCP architecture, this skill computes the blast radius and downstream structural ripple effects of proposed code modifications. Modifying or deleting core classes, function signatures, or schema definitions without knowing all callers leads to catastrophic regressions across downstream modules. This skill builds caller trees, traces dependency paths, and classifies blast severity before code is changed.

## Blast Radius Severity Classification
- **Tier 1 (Critical / Broad Blast Radius)**:
  - Public API signatures, database schema columns, shared network protocols.
  - Callers spanning multiple subsystems or external consumers (> 10 direct callers).
  - *Requirement*: Formal deprecation phase, backward-compatibility adapter, comprehensive end-to-end regression tests.
- **Tier 2 (Moderate / Subsystem Blast Radius)**:
  - Module-internal interfaces or shared helper utilities.
  - 3-10 direct callers contained within the same repository.
  - *Requirement*: Atomic refactoring across all call-sites in a single commit.
- **Tier 3 (Local / Minimal Blast Radius)**:
  - Private helper methods, local unexported variables, leaf functions.
  - 0-2 callers isolated within a single file.
  - *Requirement*: Local unit test verification.

## Analysis Workflow
1. **Identify Target Symbol**: Pinpoint exact symbol name, signature, and module path.
2. **Find Direct Inward Callers**: Query AST or static index for all functions invoking the target symbol.
3. **Trace Transitive Dependents**: Walk the caller hierarchy upward to identify affected entrypoints (APIs, CLI commands, UI handlers).
4. **Compile Downstream Impact Map**: List all affected files, line numbers, and tests needing updates.
