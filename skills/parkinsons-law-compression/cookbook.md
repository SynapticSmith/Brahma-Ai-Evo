# Cookbook: Radical Scope Compression via Artificial Constraints

## Objective
Compress a sprawling task or bloated implementation by applying strict boundaries on turns, token budgets, and lines of code.

## Prerequisites
- A complex, over-engineered task or sprawling pull request.

## Execution Recipe
1. **Set Hard Artificial Constraints**:
   - Limit turn budget: `max_turns = 4`.
   - Limit diff size: `max_lines_changed = 30`.
   - Limit prompt size: `max_chars = 1200`.
2. **Prune Speculative Features**:
   Strip all "nice-to-have" abstractions, premature helper functions, and speculative edge cases that are not directly tested.
3. **Draft the Minimal Bedrock Solution**:
   Write the simplest working code that passes the unit test using existing stdlib primitives.
4. **Compare Compressed Solution Against Sprawling Solution**:
   Evaluate whether the compressed solution achieves 95% of the value with 10% of the complexity.
5. **Verification Gate**:
   ```powershell
   python -m pytest tests -q
   python -m ruff check .
   ```
6. **Remediation**:
   If a compressed solution fails tests, add back only the specific missing check required by the failure, not the entire sprawling architecture.
