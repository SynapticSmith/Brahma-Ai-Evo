# Cookbook: Sunk Cost Evaluation & Clean Subsystem Decommissioning

## Objective
Audit an underperforming feature or architecture, decouple historical investment, and execute clean codebase deletion.

## Prerequisites
- Git repository workspace.

## Execution Recipe
1. **Execute Clean-Sheet Counterfactual Assessment**:
   Answer two diagnostic questions:
   - *Q1: If we started this project today with zero lines written, would we choose this path?*
   - *Q2: Does continuing this path yield higher ROI than alternative investments?*
   If NO to either, initiate decommissioning.
2. **Catalog Blast Radius & Usages**:
   ```powershell
   rg "\b<dead_module_or_symbol>\b" --stats
   ```
3. **Atomic Purge of Dead Subsystem**:
   Delete source files, dead tests, and unneeded dependencies in a single refactoring branch:
   ```powershell
   git rm -r src/legacy_module/ tests/test_legacy.py
   ```
4. **Run Invariant & Test Suite Verification**:
   ```powershell
   py -3.11 -m ruff check .
   py -3.11 -m pytest tests -q
   ```
5. **Verification Gate**:
   *Gate: All tests pass; 0 references remain to the decommissioned module; codebase line count decreases.*
6. **Remediation**:
   If removal breaks downstream consumers, introduce a lightweight shim or redirect callers to the canonical alternative.
