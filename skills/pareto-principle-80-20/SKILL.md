---
name: pareto-principle-80-20
description: Apply the 80/20 rule to isolate high-leverage bottlenecks, critical code paths, and top error drivers for maximum impact.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, pareto, leverage, prioritization, optimization, 80-20]
triggers:
  - "pareto principle"
  - "80 20 rule"
  - "high leverage points"
  - "find top bottlenecks"
  - "prioritize by impact"
  - "pareto analysis"
---

# Pareto Principle (80/20 Rule) Skill

## Overview
Formulated by economist Vilfredo Pareto and popularized by management consultant Joseph Juran, the Pareto Principle asserts that roughly 80% of consequences come from 20% of causes. In software engineering and AI systems, this manifests consistently:
- 80% of execution time is spent in 20% of code loops.
- 80% of crashes originate from 20% of edge-case bugs.
- 80% of user value is driven by 20% of core capabilities.
- 80% of token consumption is incurred by 20% of repetitive prompt boilerplate.

## Procedure
1. **Identify the Outcome Metric**:
   Define the exact metric to optimize (e.g. latency, token usage, crash rate, developer time).
2. **Collect and Rank Contributing Drivers**:
   Log and rank every contributor in descending order of frequency or magnitude.
3. **Isolate the Vital Few**:
   Draw the cutoff at the top 20% of items contributing the vast majority (~80%) of the metric.
4. **Focus Surgical Optimization on the Vital Few**:
   Dedicate engineering effort exclusively to the vital few; avoid premature optimization across the remaining 80% ("trivial many").
5. **Re-Measure and Re-Rank**:
   Verify that optimizing the top drivers produced the expected macroscopic improvement.
