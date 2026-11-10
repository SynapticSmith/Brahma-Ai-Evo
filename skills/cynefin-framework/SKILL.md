---
name: cynefin-framework
description: Sense-making framework categorizing problems into Clear, Complicated, Complex, Chaotic, and Confused to choose optimal engineering responses.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, cynefin, complexity, sense-making, strategy, decisions]
triggers:
  - "cynefin framework"
  - "problem complexity classification"
  - "simple vs complex problem"
  - "chaotic situation"
  - "sense making"
  - "determine problem domain"
---

# Cynefin Sense-Making Framework Skill

## Overview
Created by Dave Snowden at IBM in 1999, the Cynefin (pronounced *kuh-NEV-in*) framework helps leaders, engineers, and AI agents understand the operational context of a problem before acting:

1. **Clear / Simple (The Realm of Best Practice)**:
   - *Characteristics*: Cause and effect are directly linked and obvious to all.
   - *Protocol*: **Sense $\rightarrow$ Categorize $\rightarrow$ Respond**. Apply established best practices or standard recipes.
2. **Complicated (The Realm of Experts)**:
   - *Characteristics*: Cause and effect exist but require expert analysis, diagnostic tools, or multiple hypotheses.
   - *Protocol*: **Sense $\rightarrow$ Analyze $\rightarrow$ Respond**. Run profiling, check git history, apply systematic debugging.
3. **Complex (The Realm of Emergence)**:
   - *Characteristics*: Cause and effect can only be perceived in retrospect; unpredictable emergent behavior.
   - *Protocol*: **Probe $\rightarrow$ Sense $\rightarrow$ Respond**. Create safe-to-fail experiments, worktrees, or prototypes.
4. **Chaotic (The Realm of Rapid Response)**:
   - *Characteristics*: No discernible relationship between cause and effect; immediate crisis or active data loss.
   - *Protocol*: **Act $\rightarrow$ Sense $\rightarrow$ Respond**. Halt the bleeding immediately (kill rogue process, restore backup) before analyzing.
5. **Confused (Disorder)**:
   - *Characteristics*: Domain is unknown; participants argue over the nature of reality. Break into constituent components.
