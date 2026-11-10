# Cookbook: Iterative Skill Optimization from Failure Logs

## Objective
Analyze a failed agent execution transcript and update the corresponding skill's triggers and cookbook to prevent future failures.

## Prerequisites
- Error transcript or test failure from a previous agentic turn.
- Existing skill directory in `skills/<name>/`.

## Execution Recipe
1. **Analyze Failure Point in Transcript**:
   Locate the exact turn where the agent drifted, hallucinated arguments, or failed to shortlist the skill.
2. **Diagnose Optimization Target**:
   - If skill wasn't triggered: Append the exact user prompt phrasing to `triggers` in `SKILL.md`.
   - If tool arguments failed: Add explicit JSON / CLI argument syntax into `cookbook.md`.
   - If remediation looped: Add a concrete abort condition into `## Remediation`.
3. **Apply Edits**:
   Update `skills/<name>/SKILL.md` or `skills/<name>/cookbook.md`.
4. **Recompile Skills Index**:
   ```powershell
   python tools/skills_index.py --build
   python tools/skills_index.py --check
   ```
5. **Verification Gate**:
   Simulate the failing turn with `python -m agent run "<failing_prompt>" --provider stub --json --no-session` to ensure the updated skill shortlists and compiles into the turn plan.
6. **Remediation**:
   If the prompt exceeds the 1,200 character budget, compress verbose explanations while preserving all code and CLI examples.
