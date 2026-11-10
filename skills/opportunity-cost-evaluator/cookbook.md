# Cookbook: Evaluating Opportunity Costs of Architectural Choices

## Objective
Evaluate competing technical directions by explicitly measuring what capabilities, maintenance cycles, and simplifications are sacrificed.

## Prerequisites
- A non-trivial engineering decision with at least two viable implementation approaches.

## Execution Recipe
1. **List the Competing Alternatives**:
   - *Option A*: Build custom in-house implementation (e.g. custom AST validator).
   - *Option B*: Standardize on established stdlib / existing tool (e.g. `ast.parse` + existing `scripts_index.py`).
2. **Tabulate Costs Beyond Initial Code**:
   For each option, quantify:
   - Initial implementation turns/hours.
   - Ongoing CI maintenance and test execution time.
   - Cognitive burden on other developers/agents.
3. **Identify the True Forgone Value**:
   Ask: "If we spend 3 days building Option A, which critical roadmap features get delayed?"
4. **Choose the Maximum Net-Leverage Option**:
   Select the direction that maximizes system value per unit of ongoing maintenance risk.
5. **Verification Gate**:
   Document the trade-off in an ADR or commit message justifying why the forgone alternative was inferior.
6. **Remediation**:
   If an in-house bespoke implementation begins consuming disproportionate debugging cycles, retire it in favor of a standard solution.
