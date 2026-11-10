# Cookbook: Fullstack Scaffolding

## Objective
Scaffold a modular frontend and backend repository with clean configuration and dev tooling.

## Prerequisites
- python 3.11+ / node.js

## Execution Recipe
1. **Generate Root Structure**:
   Scaffold `backend/`, `frontend/`, `tests/`, and `config/`.
2. **Configure Package Managers**:
   Generate `pyproject.toml` or `package.json` with pinned development scripts.
3. **Scaffold Entrypoints**:
   Create minimal `main.py` and healthcheck test.
4. **Verification Gate**:
   Run the health test and ensure server starts cleanly.
5. **Remediation**:
   If dependencies conflict, lock versions in a virtual environment.
