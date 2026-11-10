---
name: desloppify
description: Transform speculative, bloated, or fragile model-generated code into clean, well-engineered, idiomatic, and robust software.
metadata:
  domain: applied-sciences/engineering-and-technology/software-engineering
  tags: [refactoring, code-quality, desloppify, clean-code, engineering]
triggers:
  - "desloppify"
  - "clean up this code"
  - "refactor slop"
  - "remove boilerplate"
---

# Desloppify Skill

## Overview
AI coding agents frequently emit "slop": speculative abstractions, redundant helper functions, defensive catch-all exception blocks, unneeded external dependencies, and verbose comments that restate the code. This skill provides an aggressive, disciplined refactoring SOP to clean and harden the codebase.

## Code Quality Rules
1. **Kill Speculative Abstractions**: If a class, factory, or generic wrapper is only used in one place, collapse it to a simple, direct function.
2. **Eliminate Catch-All Swallowing**: Replace `except Exception: pass` with explicit exception types and targeted logging. Never mask bugs.
3. **Prune Comment Noise**: Delete comments that merely narrate what the code obviously does. Keep only architectural invariants, non-obvious constraints, or security notes.
4. **Enforce Type Consistency**: Ensure all public functions have strict type hints and pass static lint checks (`ruff check`).
5. **Preserve Documentation Integrity**: Do not delete existing user-written docstrings or comments unrelated to your changes.

## Procedure
1. **Static Analysis**: Run `ruff check <target>` and `pytest <test_file>` to establish the baseline.
2. **Identification Pass**: Pinpoint redundant wrappers, unneeded layers of indirection, and dead code.
3. **Surgical Simplification**: Apply targeted edits to simplify control flow, flatten nested loops, and use stdlib primitives.
4. **Verification**: Re-run the test suite to guarantee zero regression in behavior or contract.
