# Cookbook: Problem Classification and Action Routing via Cynefin

## Objective
Classify an engineering challenge or system failure into its Cynefin domain and route execution to the correct cognitive protocol.

## Prerequisites
- A technical challenge, bug report, or system outage.

## Execution Recipe
1. **Identify the Operating Domain**:
   - Is production actively corrupting data or crashing repeatedly? $\rightarrow$ **Chaotic**.
   - Are multiple distributed systems exhibiting non-deterministic behavior? $\rightarrow$ **Complex**.
   - Is there a specific bug or performance hotspot requiring deep code inspection? $\rightarrow$ **Complicated**.
   - Is it a well-understood routine task (e.g. adding a field, linting)? $\rightarrow$ **Clear**.
2. **Execute Domain-Specific Action**:
   - **If Chaotic**: *Act first*. Kill the failing service or stop audio stream (`Ctrl+Shift+X`). Stabilize first, ask questions later.
   - **If Complex**: *Probe first*. Launch an isolated git worktree (`tools/worktree.py`) or spike prototype. Test safe-to-fail hypotheses.
   - **If Complicated**: *Analyze first*. Use `systematic-debugging` and `profiler`. Do not guess; run diagnostics.
   - **If Clear**: *Follow recipe*. Execute standard OpenMonoAgent cookbook.
3. **Prevent Domain Collapse**:
   Never treat a Complex problem as Clear (applying rigid dogma to emergent behavior causes catastrophic failure).
4. **Verification Gate**:
   Verify the chosen protocol restored stability (Chaotic $\rightarrow$ Complex $\rightarrow$ Complicated $\rightarrow$ Clear).
5. **Remediation**:
   If an approach stalls for more than 2 turns, the problem was misclassified. Shift one domain higher and adjust strategy.
