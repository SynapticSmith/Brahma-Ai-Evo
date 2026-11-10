# Cookbook: Pareto Optimization and Leverage Point Discovery

## Objective
Identify and resolve the top 20% of code paths or failure vectors causing 80% of latency, resource consumption, or bugs.

## Prerequisites
- Performance profile, test failure logs, or benchmark telemetry.

## Execution Recipe
1. **Gather Performance / Defect Distribution**:
   Run profiler or aggregate logs to identify hotspot frequencies:
   ```powershell
   python -m cProfile -s tottime main.py
   ```
2. **Sort Hotspots Descending**:
   Create a ranked table of functions by total time or error frequency.
3. **Select the Top 20% Cutoff**:
   Identify the top 1 to 3 functions that account for the bulk of execution time.
4. **Formulate High-Leverage Fix**:
   Apply algorithmic optimizations, caching, or early-exit gates strictly to those top hotspots.
   - Example: Adding an LRU cache or indexing a database column.
5. **Verification Gate**:
   Benchmark the optimized code against the baseline to prove measurable gains (> 50% improvement in targeted metric).
6. **Remediation**:
   If an optimization on a top hotspot does not move the needle, re-profile to detect whether external I/O or waiting states were misattributed.
