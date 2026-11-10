# Cookbook: Objective Decision Matrix Evaluation

## Objective
Evaluate competing technical designs or dependencies using a weighted scoring matrix to achieve a rational, defensible architecture choice.

## Prerequisites
- At least two viable technical proposals or candidate packages.

## Execution Recipe
1. **Define Candidate Set**:
   Document the options under review:
   - Candidate 1: Standard library `sqlite3` + FTS5
   - Candidate 2: In-memory dictionary + custom BM25
   - Candidate 3: External search engine daemon (e.g. Meilisearch)
2. **Assign Criteria Weights**:
   - `W1 (Zero extra install burden)`: 30%
   - `W2 (Sub-10ms query latency)`: 25%
   - `W3 (Crash resilience & persistence)`: 25%
   - `W4 (Cross-platform Python stdlib)`: 20%
3. **Score Matrix Generation**:
   Construct the scoring table (1 = Poor, 5 = Excellent):
   - SQLite FTS5: W1=5, W2=5, W3=5, W4=5 -> Total = 5.0
   - In-memory BM25: W1=5, W2=5, W3=2, W4=5 -> Total = 4.25
   - External Daemon: W1=1, W2=5, W3=5, W4=1 -> Total = 3.0
4. **Enforce Non-Negotiable Pass/Fail Gates**:
   Ensure winning candidate meets all strict constraints (e.g. must run on offline Windows client without docker).
5. **Verification Gate**:
   Implement a prototype spike with the winning candidate and verify benchmarks match matrix assumptions.
6. **Remediation**:
   If benchmark data invalidates a score, update the matrix and re-evaluate the runner-up candidate.
