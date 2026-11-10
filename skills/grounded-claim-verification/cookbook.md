# Cookbook: Codebase Claim Grounding & Provenance Audit

## Objective
Audit technical claims, anchor them with exact line-level hyperlinks, and eliminate hallucinated assertions.

## Prerequisites
- Local workspace access with grep / ripgrep.

## Execution Recipe
1. **Extract High-Risk Assertions**:
   Identify statements regarding interface contracts, dependency versions, or performance metrics.
2. **Retrieve File Provenance**:
   ```powershell
   rg -n "<symbol_or_keyword>" <directory>
   ```
3. **Format Grounded File Link**:
   Format every assertion with standard markdown file links:
   `[filename.py#L42-L48](file:///C:/path/to/file#L42-L48)`
4. **Empirical Gate Verification**:
   If claiming a test passes or fails, run the command and include the exact return code and tail output:
   ```powershell
   py -3.11 -m pytest tests/test_target.py -q
   ```
5. **Verification Gate**:
   *Gate: 100% of technical assertions are anchored by valid file hyperlinks or verifiable command outputs.*
6. **Remediation**:
   If an assertion cannot be verified on disk, mark it explicitly as "Unverified Hypothesis" or discard it entirely.
