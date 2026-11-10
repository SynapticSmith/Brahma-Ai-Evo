# Cookbook: Structural Blast Radius & Impact Analysis

## Objective
Trace inbound callers, classify blast radius severity, and identify downstream modules affected by a proposed signature change.

## Prerequisites
- Ripgrep (`rg`), Python AST analyzer, or graph index.

## Execution Recipe
1. **Search Symbol Inbound Invocations**:
   ```powershell
   python -m agent search_code "<symbol_name>"
   # or via ripgrep:
   rg "\b<symbol_name>\b" --glob "!tests/*"
   ```
2. **Classify Affected Modules & Severity**:
   - Count direct call-sites:
     - 0-2 (Tier 3 - Local)
     - 3-10 (Tier 2 - Subsystem)
     - >10 (Tier 1 - Broad / Public API)
3. **Trace Upward Entrypoints**:
   Trace which CLI commands, HTTP routes, or background workers invoke the callers.
4. **Identify Affected Test Cases**:
   ```powershell
   rg "\b<symbol_name>\b" tests/
   ```
5. **Verification Gate**:
   *Gate: Complete inventory of all callers and tests produced; no untracked dependencies remain.*
6. **Remediation**:
   If blast radius exceeds Tier 2 (cross-module breaking change), create a backward-compatible wrapper or deprecation shim rather than altering signatures in-place.
