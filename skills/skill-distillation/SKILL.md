---
name: skill-distillation
description: Distill human documentation, API guides, and execution trajectories into standardized, executable SKILL.md and cookbook.md files.
metadata:
  domain: coding
  tags: [skills, distillation, knowledge-extraction, sop, automation, meta-skill]
triggers:
  - "distill a skill"
  - "turn this doc into a skill"
  - "create skill from guide"
  - "skill distillation"
  - "extract skill from documentation"
---

# Skill Distillation Skill

## Overview
Drawn from the `Resource2Skill` framework, this meta-skill transforms unstructured human-created technical resources (manuals, API documentation, blog posts, terminal trajectories) into standardized, machine-executable agent skills.

## Distillation Contract
Every distilled skill must output two complementary artifacts:
1. **`SKILL.md` (Progressive Prose & Metadata)**:
   - Valid YAML frontmatter: `name`, `description`, `metadata.domain` (cognitive, coding, architecture, devops, security, research), `metadata.tags`, `triggers`.
   - Comprehensive prose: Conceptual background, invariants, step-by-step procedures, edge cases.
2. **`cookbook.md` (OpenMonoAgent Execution Recipe)**:
   - Tailored specifically for small local models (`lfm3:1.7b`).
   - Strict sections: `## Objective`, `## Prerequisites`, `## Execution Recipe` (numbered concrete commands), `## Verification Gate`, `## Remediation`.
   - Must fit within a 1,200 character budget for minimal token overhead.
