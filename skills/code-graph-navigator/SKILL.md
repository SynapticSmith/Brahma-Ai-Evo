---
name: code-graph-navigator
description: Monorepo AST dependency graph navigation to trace call hierarchies, caller/callee trees, and interface implementations across languages.
metadata:
  domain: coding
  tags: [ast, call-graph, dependency-tree, code-navigation, monorepo, refactoring]
triggers:
  - "code graph navigator"
  - "trace call hierarchy"
  - "find all callers"
  - "map dependencies"
  - "code graph analysis"
  - "ast call graph"
---

# Code Graph Navigator Skill

## Overview
Drawn from `code-graph-rag` and `graphify`, this skill overcomes the limitations of naive keyword text search (`grep`) in large codebases. Instead of matching text substrings, it parses Abstract Syntax Trees (ASTs) to construct a deterministic graph of symbols:
- **Call Trees**: Who calls this function, and what does this function call?
- **Inheritance Graphs**: Which classes inherit from this ABC or implement this protocol?
- **Module Coupling**: Which modules import this package, and what are the cycle risks?

## Core Rules
1. **Language-Specific AST Parsers**: Use Python's stdlib `ast` for Python; tree-sitter or regex-tokenizers for polyglot repos.
2. **Deterministic Resolution**: Differentiate between method names shared across unrelated classes.
3. **Cycle Detection**: Identify circular imports before initiating refactoring operations.
