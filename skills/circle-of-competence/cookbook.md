# Cookbook: Epistemic Boundary Check and Truthfulness Gate

## Objective
Prevent hallucinations and maintain strict truthfulness by verifying that required knowledge exists in local state before generating answers.

## Prerequisites
- User query requiring specific factual, architectural, or doctrinal knowledge.

## Execution Recipe
1. **Assess Boundary Ownership**:
   Ask: "Does the repository, second-brain vault, or attached tools contain ground-truth data for this query?"
   - If YES $\rightarrow$ Proceed to step 2.
   - If NO $\rightarrow$ Proceed to step 4.
2. **Retrieve Verified Evidence**:
   Query local second brain or AST symbols:
   ```python
   # Execute brain_search or grep_files to fetch verified sources
   ```
3. **Anchor Answer Strictly to Cited Evidence**:
   Every non-trivial claim must reference an exact file path, function signature, or verified source.
4. **If Outside Competence Boundary**:
   State the limitation directly:
   - "This inquiry falls outside the local repository and indexed second brain."
   - Provide the exact search command or external verification step for the user.
5. **Verification Gate**:
   - 0 fabricated function names or nonexistent file paths.
   - All factual assertions link to valid local or retrieved references.
6. **Remediation**:
   If an answer begins guessing about external systems, halt and replace speculation with an explicit question or research action.
