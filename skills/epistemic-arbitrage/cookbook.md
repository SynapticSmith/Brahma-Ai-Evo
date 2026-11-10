# Cookbook: Epistemic Arbitrage & Context Gap Bridging

## Objective
Identify where a task depends on local ground truth vs general reasoning, and inject targeted context to eliminate model hallucinations.

## Prerequisites
- Complex reasoning or architectural refactoring task.

## Execution Recipe
1. **Classify Epistemic Requirements**:
   - Class A (General Knowledge): Standard algorithms, RFC protocols -> *Handled by model alone.*
   - Class B (Local Facts): Function signatures, schema, config -> *Must inject exact file text.*
   - Class C (User Intent): Strategic priority, risk tolerance -> *Must confirm via question.*
2. **Retrieve & Anchor Local Facts Before Prompting**:
   Read exact file contents or run AST search:
   ```powershell
   python -m agent search_code "<target_symbol>"
   ```
3. **Formulate High-Leverage Arbitrage Prompt**:
   Feed verified local context (Class B) into the model's generalized reasoning engine (Class A):
   `"Given this verified schema <SCHEMA>, generate an optimized query following RFC 3339."`
4. **Verification Gate**:
   *Gate: Zero hallucinated class names or methods; output directly runs on the local repo.*
5. **Remediation**:
   If the model invents a non-existent parameter, prune model discretion by supplying a strict interface contract.
