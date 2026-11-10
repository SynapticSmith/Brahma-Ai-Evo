# Cookbook: Technical Book Distillation to Agent Skill

## Objective
Convert a technical book chapter or manual into a standardized `SKILL.md` and small-model `cookbook.md`.

## Prerequisites
- Markdown or text extraction of the source book (`markitdown` or OCR).

## Execution Recipe
1. **Extract Core Architectural Invariants**:
   Identify author-mandated constraints: rules that must never be broken (e.g. "always validate schema", "never catch generic Exception").
2. **Draft YAML Frontmatter**:
   ```yaml
   ---
   name: <kebab-slug>
   description: <one-sentence summary>
   metadata:
     domain: <canonical-domain>
     tags: [<tag1>, <tag2>]
   triggers: ["<trigger 1>", "<trigger 2>"]
   ---
   ```
3. **Synthesize Progressive Disclosure Body**:
   Write `SKILL.md` with Overview, Invariant Rules, and Multi-Step Procedure.
4. **Author 5-Header Compact Cookbook**:
   Write `cookbook.md` ($\le 1,200$ chars) containing:
   - Objective & Prerequisites
   - Execution Recipe with concrete CLI commands
   - Verification Gate with quantitative pass condition
   - Remediation branch
5. **Verification Gate**:
   *Gate: `python tools/skills_index.py --check` passes cleanly for the new skill directory.*
6. **Remediation**:
   If validation fails on YAML or missing headers, adjust frontmatter formatting and ensure exact header spelling.
