---
name: cli-wrapper-generator
description: Wrap arbitrary command-line binaries and utility scripts into clean, agent-native tool contracts with JSON output and containment.
metadata:
  domain: coding
  tags: [cli, wrapper, tool-contract, json, agent-native, integration]
triggers:
  - "cli wrapper generator"
  - "make cli agent native"
  - "wrap binary into tool"
  - "cli tool contract"
  - "structured cli wrapper"
---

# CLI Wrapper Generator Skill

## Overview
Drawn from the `CLI-Anything` framework, this skill proceduralizes the integration of legacy or external command-line binaries into agent-native tools. Instead of letting agents invoke raw, unstructured shell commands (which risk leaking grandchild processes, blocking on interactive prompts, or producing unparseable stdout), this skill wraps binaries with:
1. **Machine-Readable JSON Output**: Automatically parses unstructured stdout/stderr into validated JSON schemas.
2. **Deterministic Exit Codes**: Maps error conditions to standard exit codes (0=success, 2=invalid arguments, 4=runtime error).
3. **Execution Containment**: Runs through `agent.process.run_captured` with closed stdin and Job Object lifetime termination.
4. **AST Safety Classification**: Classifies the tool into `read_only`, `workspace_write`, `network`, or `destructive`.
