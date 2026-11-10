---
name: spec-driven-development
description: Spec-Driven Development (SDD) methodology to specify architecture, contracts, invariants, and edge cases before coding.
metadata:
  domain: architecture
  tags: [sdd, specification, contracts, design-document, architecture, spec-first]
triggers:
  - "spec driven development"
  - "write a spec"
  - "software design document"
  - "sdd planning"
  - "spec first"
  - "design document"
---

# Spec-Driven Development (SDD) Skill

## Overview
Drawn from the `spec-kit` framework, SDD shifts intellectual effort from debugging to upfront design. By declaring system constraints, data contracts, and failure boundaries in a formal specification before writing code, implementation becomes mechanical and verification becomes deterministic.

## Required Specification Sections
1. **Context & Motivation**: Why does this subsystem need to exist? What problem is solved?
2. **System Boundaries & Non-Goals**: What is explicitly out of scope?
3. **Data Schema & Models**: Field types, optionality, nullability, serialization formats.
4. **Interface Contracts & Verbs**: Invocable functions/tools, argument types, return payloads, error statuses.
5. **Architectural Invariants**: Hard rules that must never be violated (e.g. stdlib-only, single-entry permission gates).
6. **Failure Modes & Edge Cases**: Network loss, disk exhaustion, corrupt inputs, concurrent access.
7. **Acceptance Criteria & Test Matrix**: Explicit pytest assertions required for signoff.
