# Cookbook: Code Desloppification & Clean Refactoring

## Objective
Purge code slop, eliminate dead branches, enforce strict type hints, and align with project architecture.

## Prerequisites
- python 3.11+
- ruff

## Execution Recipe
1. **Audit Complexity & Unused Code**:
   ```powershell
   python -m ruff check . --select F,E,W,UP,I
   ```
2. **Format & Sort Imports**:
   ```powershell
   python -m ruff check --fix .
   ```
3. **Enforce Type Invariants & Clean Signatures**:
   Remove wildcard imports (`from x import *`), add return annotations, and eliminate duplicate logic.
4. **Verification Gate**:
   ```powershell
   python -m ruff check .
   python -m pytest tests -q
   ```
5. **Remediation**:
   If refactoring breaks tests, revert via `git checkout -p` and perform targeted edits.
