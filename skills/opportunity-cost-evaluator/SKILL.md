---
name: opportunity-cost-evaluator
description: Economic evaluation framework assessing the forgone alternatives, maintenance burdens, and sacrificed capabilities of a technical decision.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, opportunity-cost, trade-offs, economics, decision-making]
triggers:
  - "opportunity cost"
  - "what are we giving up"
  - "forgone alternatives"
  - "evaluate technical sacrifice"
  - "opportunity cost analysis"
---

# Opportunity Cost Evaluator Skill

## Overview
A foundational concept in economics: *"The real cost of anything is what you must give up to get it."*

In software engineering, every technical choice carries an invisible price tag: the alternative features, refactorings, or bug fixes that *cannot* be built because resources (time, token budget, cognitive bandwidth, maintenance overhead) are consumed by the chosen direction. Implementing a custom ORM means not building user-facing features; adding a complex caching layer means incurring permanent cache-invalidation maintenance costs.

## Core Rules
1. **Never Ask "Is this good?"**: Always ask: *"Compared to what alternative is this the best use of effort?"*
2. **Account for the Maintenance Tail**: Building a feature is 20% of the cost; maintaining, testing, updating, and documenting it across future years is 80%.
3. **Explicitly Name the Sacrificed Path**: In architectural decision records (ADRs), document the rejected options and the exact rationale for discarding them.
