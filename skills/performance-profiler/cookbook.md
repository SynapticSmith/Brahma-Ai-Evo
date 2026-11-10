# Cookbook: Performance Profiling & Optimization

## Objective
Profile execution time and memory allocations to identify and remediate bottlenecks.

## Prerequisites
- python 3.11+
- cProfile

## Execution Recipe
1. **Run cProfile on Target Code**:
   ```powershell
   python -m cProfile -s cumulative <script.py>
   ```
2. **Analyze Hot Paths**:
   Identify functions consuming > 80% of execution time.
3. **Apply Targeted Optimization**:
   Replace repeated allocations with generators, vectorize math, or add caching.
4. **Verification Gate**:
   Re-run profile and verify execution time reduction (> 20%).
5. **Remediation**:
   If optimization changes outputs, run unit test suite to detect regressions.
