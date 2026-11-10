---
name: triangulation-validation
description: Multi-source validation protocol requiring technical claims and bug diagnoses to be verified by at least 3 independent methods.
metadata:
  domain: research
  tags: [thinking, reasoning, triangulation, verification, multi-source, proof, truthfulness]
triggers:
  - "triangulation validation"
  - "verify with 3 sources"
  - "triangulate this finding"
  - "multi source proof"
  - "independent verification"
---

# Triangulation Validation Skill

## Overview
Drawn from navigation, surveying, and empirical research methodology: a single data point is an anecdote; two points suggest a line; three independent reference points fix a precise, unassailable position in space.

In software debugging and knowledge extraction, relying on a single signal (e.g. one grep match, one unit test, or a single web search snippet) frequently produces false confidence. Triangulation requires cross-verifying a critical claim across at least three distinct methods:
1. **Source Code Inspection**: Ground-truth AST syntax and variable definitions.
2. **Empirical Execution**: Live runtime testing, exit codes, and output logs.
3. **External / Historical Reference**: Git blame commit history, official documentation, or formal specification contracts.

## Rules
- Never mark a high-severity bug resolved based on code inspection alone; require runtime execution proof.
- Never assert a library capability without checking installation, import, and live execution.
