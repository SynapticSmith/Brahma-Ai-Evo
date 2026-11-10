# Cookbook: Security Audit & Secret Scanning

## Objective
Scan codebase for hardcoded credentials, API keys, unsafe subprocess calls, and known vulnerabilities.

## Prerequisites
- git
- python 3.11+

## Execution Recipe
1. **Scan for Hardcoded Secrets**:
   Search for common key prefixes (`AIza`, `sk-`, `ghp_`, `Bearer`):
   ```powershell
   git grep -E "(AIza|sk-[a-zA-Z0-9]{20,}|ghp_)"
   ```
2. **Audit Shell Invocations**:
   Check for `shell=True` without sanitization in python files:
   ```powershell
   git grep "shell=True"
   ```
3. **Verify File Permissions & Sandbox Policies**:
   Inspect `config/agent_permissions.example.json` to ensure least-privilege defaults.
4. **Verification Gate**:
   Verify 0 exposed secrets and all subprocess executions use array arguments (`shell=False`).
5. **Remediation**:
   Move detected keys to environment variables and scrub git history if needed.
