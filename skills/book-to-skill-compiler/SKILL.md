---
name: book-to-skill-compiler
description: End-to-end framework to distill technical books, manuals, and PDF guides into reusable, executable agent skills with progressive disclosure and small-model cookbooks.
metadata:
  domain: methodology
  tags: [skill-distillation, book-to-skill, learning, technical-books, sop-compiler, methodology]
triggers:
  - "book to skill compiler"
  - "compile book into skill"
  - "distill technical book"
  - "turn book into skill"
  - "extract actionable skill from book"
  - "book to skill"
  - "distill book"
---

# Book-to-Skill Compiler Skill

## Overview
Drawn from `book-to-skill` and `Resource2Skill`, this skill automates the extraction and distillation of comprehensive technical books into production-grade agent skills. Full-length technical books contain hundreds of pages of narrative context, historical background, and redundant explanations that overwhelm LLM context windows. This skill extracts core mental models, operational invariants, diagnostic decision trees, and transforms them into standard `SKILL.md` and executable `cookbook.md` assets.

## Distillation Hierarchy
1. **L0 Intent & Metadata**: Extract the core capability slug, 1-2 sentence description, canonical domain, and exact trigger phrases.
2. **L1 Invariants & First Principles**: Identify non-negotiable architectural rules, anti-patterns, and safety constraints advocated by the author.
3. **L2 Diagnostic & Execution Flow**: Reconstruct procedural workflows from book examples into numbered algorithmic steps.
4. **L3 Actionable Cookbook Recipe**: Condense the primary procedure into a $\le 1,200$ character recipe with Objective, Prerequisites, Execution, Verification, and Remediation.

## Distillation Invariants
1. **Actionable Over Historical**: Omit narrative anecdotes, author biographies, and historical trivia; retain strictly operational guidelines.
2. **Standard Headers Mandatory**: Cookbooks must adhere strictly to the 5 canonical headers.
3. **Small Model Feasibility**: Verify that the generated cookbook contains explicit CLI commands or code templates that small local models (`1.7B`) can execute without hallucination.
