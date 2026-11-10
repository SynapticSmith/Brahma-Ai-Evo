---
name: parkinsons-law-compression
description: Force radical simplicity and eliminate scope creep by imposing strict artificial constraints on token budgets, turns, and diff sizes.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, parkinsons-law, compression, scoping, simplicity, constraints]
triggers:
  - "parkinsons law compression"
  - "force radical simplicity"
  - "tighten budget constraints"
  - "cut scope creep"
  - "compressed execution"
---

# Parkinson's Law Compression Skill

## Overview
Articulated by British historian Cyril Northcote Parkinson in 1955: *"Work expands so as to fill the time available for its completion."*

In AI agent engineering and software projects, operations without strict artificial boundaries inevitably expand into massive architectural sprawl, bloated prompts, recursive helper classes, and runaway turns. Imposing severe, deliberate constraints (strict token budgets, maximum 12-turn limits, 50-line diff limits) forces agents and engineers to discard superficial complexity and find the simplest possible bedrock solution.

## Core Constraints
1. **The Turn Ceiling**: Hard cap of 12 turns on agent subtasks (Spec §I-08). If a task cannot finish in 12 turns, its scope is too wide; decompose it.
2. **The Prompt Budget Ceiling**: L2 cookbooks capped at 1,200 characters; system prompts capped to strictly machine-necessary context.
3. **The Minimal Diff Rule**: Prefer a 5-line surgical edit over a 200-line rewrite that touches unrelated code.
