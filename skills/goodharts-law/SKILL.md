---
name: goodharts-law
description: Invariant auditing framework to prevent metric fixation when an optimization measure becomes a target, it ceases to be a good measure.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, goodharts-law, metrics, perverse-incentives, optimization, quality]
triggers:
  - "goodharts law"
  - "metric fixation"
  - "perverse incentive"
  - "gaming metrics"
  - "audit target metric"
---

# Goodhart's Law Skill

## Overview
Formulated by British economist Charles Goodhart in 1975 and generalized by Marilyn Strathern: *"When a measure becomes a target, it ceases to be a good measure."*

In software engineering and AI systems, targeting a single quantitative metric inevitably leads to optimization pathology or "gaming":
- **Lines of Code Target**: Encourages verbose, bloated implementations to look productive, or unreadable golfing to stay under ceilings.
- **Code Coverage Target**: Encourages writing trivial assertions (`assert true`) that execute code paths without verifying correctness.
- **Velocity / Turn Count Target**: Encourages cutting corners on verification and documentation to finish turns quickly.

## Auditing Invariants
1. **Never Optimize a Single Metric in Isolation**: Pair every volume metric with a quality/correctness counter-weight (e.g. Line count ceilings paired with 0 linter violations and comprehensive test suites).
2. **Audit for Perverse Incentives**: Before setting a KPI or architectural ratchet, ask: *"How could a lazy agent or developer game this rule without delivering actual value?"*
3. **Preserve Substantive Meaning Over Cosmetic Numbers**: If shortening a file requires obfuscating logic into unreadable lambdas, refactor into clean sub-modules instead of golfing.
