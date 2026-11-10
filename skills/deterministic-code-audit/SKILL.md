---
name: deterministic-code-audit
description: Static security and reliability audit inspecting code for NPEs, thread safety, injection attacks, and resource leaks.
metadata:
  domain: security
  tags: [code-audit, security, static-analysis, vulnerabilities, sqli, npe, thread-safety]
triggers:
  - "deterministic code audit"
  - "audit security vulnerabilities"
  - "scan for npe"
  - "audit injection flaws"
  - "static code analysis"
  - "security sanity check"
---

# Deterministic Code Audit Skill

## Overview
Drawn from the `open-code-review` framework, this skill pairs deterministic pattern matching with AST static analysis to audit code changes before merge. It systematically searches for high-severity vulnerability patterns across multiple programming languages (Python, Go, JavaScript, C/C++).

## Key Inspection Rules
1. **Null/None Dereference (NPE)**:
   - Accessing dictionary keys without `.get()`, missing `is None` checks on optional returns.
2. **Injection Flaws**:
   - SQL: Unparameterized string interpolation in SQL queries (`f"SELECT ... WHERE id = {val}"`).
   - Command: `subprocess.run(..., shell=True)` with unescaped variables.
   - Path Traversal: Using unsanitized user strings in `open()` or `Path()` without `.resolve()` checks against root boundaries.
3. **Concurrency & Thread Safety**:
   - Accessing shared data structures across threads without acquiring locks.
   - Signal/slot mutations outside the GUI main thread.
4. **Resource Management**:
   - Sockets, file descriptors, database connections opened without `with` context managers or `finally` blocks.
