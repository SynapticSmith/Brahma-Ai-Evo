# Cookbook: Decision Triage via Reversibility Analysis

## Objective
Classify a pending engineering choice as Type 1 or Type 2, match it with the correct decision process, and prevent unnecessary paralysis on reversible items.

## Prerequisites
- A proposed technical change, architectural decision, or implementation direction.

## Execution Recipe
1. **Assess Reversibility Cost**:
   Ask: "If this change fails completely in production, how long does it take to restore the prior state and what is the blast radius?"
   - If rollback requires > 1 day or causes permanent data loss $\rightarrow$ **Type 1 (One-Way Door)**.
   - If rollback requires `git revert` or restoring a backup table $\rightarrow$ **Type 2 (Two-Way Door)**.
2. **If Type 1 (One-Way Door)**:
   - Halt fast execution.
   - Author a formal spec using `spec-driven-development`.
   - Run a `pre-mortem-analysis` to map failure vectors.
   - Require explicit human/supervisor review before merging.
3. **If Type 2 (Two-Way Door)**:
   - Maximize decision velocity.
   - Write a quick unit test, apply the change, run verification.
   - Ship and iterate.
4. **Build the Rollback Handle**:
   Even for Type 2 decisions, ensure the rollback path is verified (e.g. clean git tree, database transaction).
5. **Verification Gate**:
   Type 2 changes must pass existing CI gates (`pytest`, `ruff`) without requiring architectural ratchets or policy changes.
6. **Remediation**:
   If a Type 2 decision turns out to have hidden one-way dependencies, immediately pause, roll back, and re-classify as Type 1.
