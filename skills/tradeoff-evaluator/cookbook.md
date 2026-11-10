# Cookbook: Trade-Off Balancing and Boundary Enforcement

## Objective
Quantify and enforce boundaries on engineering compromises to prevent uncontrolled resource exhaustion or latency degradation.

## Prerequisites
- A proposed optimization that trades one system property for another.

## Execution Recipe
1. **Define the Trade-Off Statement**:
   Fill in the template: "We are trading [Property A: e.g., Disk Space] to maximize [Property B: e.g., Turn Context Assembly Speed]."
2. **Measure Baseline Metrics**:
   Record initial benchmarks:
   - Peak RAM usage: `<x> MB`
   - Assembly Latency: `<y> ms`
   - Disk footprint: `<z> MB`
3. **Set Hard Ceiling on Sacrificed Metric**:
   Establish maximum allowable tolerance for the sacrificed dimension (e.g., Disk cache must never exceed 250MB).
4. **Implement Automatic Throttling / Eviction**:
   Write cleanup logic that monitors the metric and evicts oldest items when 90% of tolerance is reached:
   ```python
   if current_disk_usage > MAX_CACHE_BYTES * 0.9:
       prune_oldest_cache_entries()
   ```
5. **Verification Gate**:
   Execute a stress test pushing data past the threshold; assert that memory or disk never exceeds the ceiling and performance gracefully degrades rather than crashing:
   ```powershell
   python -m pytest tests -q -k "stress or cache"
   ```
6. **Remediation**:
   If the ceiling is breached, switch from in-process storage to compressed on-disk SQLite or memory-mapped files.
