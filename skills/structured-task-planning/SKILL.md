---
name: structured-task-planning
description: Deconstruct complex features or migrations into dependency-ordered atomic subtasks with explicit preconditions and verification gates.
metadata:
  domain: architecture
  tags: [planning, task-breakdown, dependencies, workflow, execution, project-management]
triggers:
  - "task planning"
  - "break down this task"
  - "plan implementation"
  - "execution plan"
  - "dependency order"
  - "feature breakdown"
---

# Structured Task Planning Skill

## Overview
Drawn from the `superpowers` framework, this skill structures large, ambiguous programming objectives into deterministic, step-by-step roadmaps:
1. **Scope Definition**: Establish strict boundaries for what is in-scope and out-of-scope.
2. **Dependency Graph Construction**: Model tasks as a Directed Acyclic Graph (DAG) where foundational seams are built before consumer modules.
3. **Atomic Task Units**: Each subtask must be small enough to complete in a single turn/commit, with its own unit test verification gate.
4. **Pre-Flight Invariant Checking**: Validate environment tools, dependencies, and file paths before initiating writes.

## Planning Principles
- **Bottom-Up Construction**: Build models/schemas first, logic/controllers second, and UI/CLI surfaces last.
- **Continuous Verifiability**: Never leave the repository in a broken intermediate state between tasks.
- **Explicit Rollback Hooks**: Every task must document how to undo its changes if a downstream blocker emerges.
