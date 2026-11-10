# Cookbook: ICM Workspace Structuring

## Objective
Organize problem spaces into deterministic, filesystem-backed agent architectures.

## Prerequisites
- python 3.11+

## Execution Recipe
1. **Analyze Topology**:
   Map input sources, intermediate staging zones, and permanent asset stores.
2. **Scaffold Directory Layout**:
   Create `input/`, `staging/`, `canonical/`, and `config/` directories.
3. **Define Routing Rules**:
   Write declarative JSON or YAML index defining transitions between stages.
4. **Verification Gate**:
   Verify that each stage contains a README or index.md defining its contracts.
5. **Remediation**:
   If notes linger in staging, trigger extraction router to resolve destination.
