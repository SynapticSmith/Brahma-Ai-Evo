---
name: skill-optimizer
description: Continuous text-space optimizer refining skill prompts, triggers, and cookbook steps based on empirical agent execution trajectories.
metadata:
  domain: coding
  tags: [skills, optimization, trajectory-analysis, self-improvement, continuous-learning]
triggers:
  - "optimize this skill"
  - "refine skill prompt"
  - "skill optimizer"
  - "improve cookbook"
  - "tune skill triggers"
---

# Skill Optimizer Skill

## Overview
Drawn from the `SkillOpt` framework, this meta-skill implements text-space trajectory-driven prompt optimization. Instead of treating skill descriptions and cookbooks as static text, it analyzes execution failures, agent misunderstandings, and tool call rejections to iteratively refine prompts, preconditions, and remediation instructions.

## Optimization Loop
1. **Trajectory Inspection**: Extract the turn transcript of an agent that failed to execute a task correctly.
2. **Failure Categorization**:
   - *Trigger Failure*: The skill was not shortlisted when it should have been (missing trigger phrases).
   - *Precondition Failure*: The agent attempted to run commands without required tools installed.
   - *Parameter Drift*: The agent invoked tool functions with hallucinated or malformed argument names.
   - *Hallucinated Recovery*: The agent looped in remediation without checking the true failure signal.
3. **Targeted Text Mutation**:
   - Add the missed user phrase to `triggers`.
   - Tighten the cookbook recipe with explicit argument examples.
   - Add an early-exit invariant to the remediation step.
4. **Validation Gate**: Re-run the turn against the updated skill to verify zero-regression execution.
