# Cookbook: Loss Aversion Audit & Symmetric Expected Value Scoring

## Objective
Neutralize loss aversion in architectural decisions by calculating symmetric Expected Value and bounding downside risk.

## Prerequisites
- A proposed high-impact refactoring or technology migration.

## Execution Recipe
1. **Catalog Prospective Gains & Potential Losses**:
   - Gains: Execution speedup, test run reduction, code simplification, developer velocity.
   - Losses: Regression risk, migration effort, transient deployment bugs.
2. **Compute Symmetric Expected Value (EV)**:
   ```
   EV = (Prob_Gain * Value_Gain) - (Prob_Loss * Cost_Loss)
   ```
   *Rule: Score probabilities objectively based on automated test coverage.*
3. **Bound Downside via Two-Way Door Mechanism**:
   Design an instant rollback mechanism to reduce `Cost_Loss` to near-zero:
   - Git worktree isolation: `git worktree add ../migration-test`
   - Config feature flag: `ENABLE_V2_PIPELINE = false`
4. **Verification Gate**:
   *Gate: EV > 0 and worst-case recovery time is bounded to < 5 minutes via git or feature flag.*
5. **Remediation**:
   If team hesitation persists despite positive EV, run a 24-hour shadow test without altering production traffic.
