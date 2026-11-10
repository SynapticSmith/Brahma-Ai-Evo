# Cookbook: Progressive Context Window Optimization

## Objective
Reduce LLM prompt bloat and enforce deterministic progressive disclosure (L0 to L3) for sub-2B small models and cloud agents alike.

## Prerequisites
- An agent loop or prompt assembly pipeline.

## Execution Recipe
1. **Measure Baseline Turn Context**:
   Profile the prompt payload:
   ```python
   prompt_chars = len(assembled_prompt)
   estimated_tokens = prompt_chars // 4
   print(f"Context payload: {estimated_tokens} tokens ({prompt_chars} chars)")
   ```
2. **Audit Component Token Shares**:
   Verify individual component thresholds:
   - System baseline: <= 1,000 tokens
   - Tool declarations: <= 2,500 tokens (defer low-priority tools)
   - Second-brain JIT snippet: <= 800 tokens
   - Skills cookbook: <= 1,200 tokens
3. **Apply Dynamic Tool Pruning**:
   If tool declarations exceed threshold, filter by turn intent: advertise primary tools and defer specialized tools to `search_tools`.
4. **Enforce Cookbook Clamping**:
   Clamp injected SOP/cookbook to max 4KB (`cookbook[:4096]`).
5. **Verification Gate**:
   Assert that total prompt token size is under the target model's optimal window without sacrificing functional accuracy:
   ```powershell
   python -m pytest tests -q -k "turn_context or prompt"
   ```
6. **Remediation**:
   If a prompt exceeds the token ceiling, discard low-relevance conversation history turns before truncating system instructions or active cookbooks.
