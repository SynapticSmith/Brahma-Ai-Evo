---
name: decision-matrix-analysis
description: Multi-Criteria Decision Analysis (Pugh Matrix) to objectively score and rank competing technical approaches.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, decision-matrix, pugh-matrix, trade-offs, evaluation]
triggers:
  - "decision matrix"
  - "pugh matrix"
  - "compare alternatives"
  - "multi criteria analysis"
  - "which option is best"
---

# Decision Matrix Analysis Skill

## Overview
Based on Stuart Pugh's Controlled Convergence method and Multi-Criteria Decision Analysis (MCDA). When choosing between architectural patterns, storage engines, libraries, or algorithms, replace subjective bias with an objective, weighted scoring matrix evaluated across concrete engineering criteria.

## Procedure
1. **Identify Competing Candidates**:
   - List 2 to 4 viable architectural alternatives (e.g., Option A: SQLite FTS5, Option B: Whoosh pure-Python, Option C: BM25 in-memory).
2. **Establish Criteria & Normalized Weights**:
   - Define 4 to 6 critical requirements:
     - Performance (Latency / Throughput)
     - Dependency Burden (Stdlib vs 3rd-party)
     - Memory Footprint
     - Maintenance Complexity
     - Portability / Cross-Platform support
   - Assign percentage weights summing to 100% (e.g. 30%, 25%, 20%, 15%, 10%).
3. **Score Candidates Across Criteria**:
   - Score each candidate on a scale of 1 to 5 against each criterion based on measured benchmarks or verifiable facts.
4. **Calculate Weighted Total & Sensitivity Analysis**:
   - Calculate `Score = Sum(Weight_i * Rating_i)`.
   - Test sensitivity: If top priority changes, does the winning option remain robust?
