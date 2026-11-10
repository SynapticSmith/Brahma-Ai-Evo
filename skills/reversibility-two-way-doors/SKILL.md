---
name: reversibility-two-way-doors
description: Jeff Bezos decision framework classifying decisions into irreversible Type 1 (one-way doors) vs reversible Type 2 (two-way doors).
metadata:
  domain: cognitive
  tags: [thinking, reasoning, decisions, reversibility, two-way-doors, velocity, risk-management]
triggers:
  - "two way doors"
  - "one way door"
  - "reversibility analysis"
  - "type 1 type 2 decisions"
  - "irreversible decision"
  - "decision velocity"
---

# Reversibility & Two-Way Doors Skill

## Overview
Popularized by Jeff Bezos in Amazon's 1997 and 2015 shareholder letters:
- **Type 1 Decisions (One-Way Doors)**: Irreversible or nearly irreversible actions. Walking through them is consequential; if you do not like what you see on the other side, you cannot easily walk back. These decisions require deep deliberation, formal specifications, red-teaming, and broad alignment.
- **Type 2 Decisions (Two-Way Doors)**: Highly reversible actions. If you walk through and the outcome is sub-optimal, you can simply reopen the door and walk back. These decisions should be made rapidly by small autonomous teams or individual agents without bureaucratic overhead.

## Classification Guide
| Dimension | Type 1 (One-Way Door) | Type 2 (Two-Way Door) |
|:---|:---|:---|
| **Reversibility** | Extremely costly or impossible to undo | Reversible in minutes/hours via git/rollback |
| **Examples** | Database engine migration, public API breaking change, cryptographic key architecture | Prompt wording, internal helper refactor, UI styling, adding a unit test |
| **Pace** | Methodical, high-assurance, multi-round review | Fast, high-velocity, lightweight verification |
| **Process** | Spec-first (SDD), pre-mortem analysis, teacher approval | Branch, edit, run test suite, commit |
