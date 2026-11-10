---
name: grounded-claim-verification
description: Rigorous epistemic grounding and verification protocol ensuring every assertion about codebase architecture, performance, or behavior is grounded in line-level file provenance.
metadata:
  domain: cognitive
  tags: [grounding, verification, epistemic-humility, provenance, citations, claims, cognitive]
triggers:
  - "grounded claim verification"
  - "verify claims"
  - "ground assertions"
  - "provenance check"
  - "verify codebase facts"
  - "grounded intelligence"
---

# Grounded Claim Verification Skill

## Overview
Drawn from `Astrolabe`, this skill enforces full-state epistemic verification for AI coding agents. Unverified assumptions, outdated mental maps, and confident hallucinations corrupt architecture specs and lead to broken refactorings. This skill mandates that any statement regarding function behavior, call order, performance numbers, or configuration must be directly grounded in line-level code provenance, AST facts, or empirical test executions.

## Epistemic Guardrails
1. **Zero Unreferenced Assertions**: Never claim a component does or does not support a feature without linking to the exact file path and line number (`file://...#L10-L20`).
2. **Empirical Measurement Over Theory**: Never assert execution time, latency, or throughput without executing a benchmark and citing command stdout.
3. **Negative Claim Validation**: Asserting that a symbol "does not exist" or "is unused" requires an exhaustive ripgrep (`rg`) or AST call-graph search proof.
4. **Distinguish Spec from Reality**: Explicitly separate designed/intended behavior (`docs/`) from implemented behavior in source code (`src/` / `agent/`).

## Verification Workflow
1. **Claim Extraction**: Identify every factual claim in the proposed answer or architectural diff.
2. **Provenance Traversal**: Locate the definitive source of truth on disk (source code, test suite, or config).
3. **Anchor Line References**: Annotate claims with exact markdown hyperlinks to file paths and line ranges.
4. **Empirical Sanity Check**: Run unit tests or inspect AST to confirm the code still matches the stated invariant.
