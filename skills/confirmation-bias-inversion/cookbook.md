# Cookbook: Popperian Falsification & Adversarial Negative Testing

## Objective
Author negative edge cases and adversarial counter-tests to actively falsify engineering assumptions.

## Prerequisites
- Pytest environment (`pytest`, `hypothesis`).

## Execution Recipe
1. **Identify the Core Assumption**:
   Formulate statement: `"This function handles all valid user input strings."`
2. **Author 4 Falsification Negative Test Cases**:
   ```python
   import pytest

   def test_falsification_edge_cases():
       # Case 1: Extreme boundary length (10MB string)
       with pytest.raises((ValueError, MemoryError)):
           process_input("A" * 10_000_000)

       # Case 2: Null and control characters
       with pytest.raises(ValueError):
           process_input("\x00\r\n\t")

       # Case 3: Unicode RTL override & zero-width spaces
       assert process_input("\u202E\u200Bmalicious") != ""

       # Case 4: SQL/Shell metacharacters
       res = process_input("'; DROP TABLE users; --")
       assert "DROP TABLE" not in res # Must be safely escaped
   ```
3. **Execute Adversarial Test Suite**:
   ```powershell
   py -3.11 -m pytest tests/test_falsification.py -v
   ```
4. **Verification Gate**:
   *Gate: All negative and malformed inputs are caught gracefully; zero unhandled 500 exceptions.*
5. **Remediation**:
   If an adversarial input crashes the process, add strict type boundary assertions at the function entrypoint.
