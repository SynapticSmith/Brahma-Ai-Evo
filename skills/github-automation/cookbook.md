# Cookbook: GitHub Actions Workflow Creation

## Objective
Configure GitHub Actions workflows for automated testing, linting, and continuous integration.

## Prerequisites
- git

## Execution Recipe
1. **Scaffold Workflow Directory**:
   Create `.github/workflows/` if not present.
2. **Draft CI Workflow (`.github/workflows/ci.yml`)**:
   Include triggers (`on: [push, pull_request]`), OS matrix, Python setup, and test runner steps.
3. **Verify YAML Syntax**:
   ```powershell
   python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
   ```
4. **Verification Gate**:
   Verify workflow file exists, passes YAML parser, and references valid branch names.
5. **Remediation**:
   Fix indentation errors if YAML parsing fails.
