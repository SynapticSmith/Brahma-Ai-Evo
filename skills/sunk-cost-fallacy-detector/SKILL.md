---
name: sunk-cost-fallacy-detector
description: Cognitive strategic framework auditing software initiatives to detect sunk-cost traps, decouple historical investment from future value, and decommission dead ends.
metadata:
  domain: cognitive
  tags: [mental-model, sunk-cost, decision-making, cognitive, refactoring, decommissioning, strategy]
triggers:
  - "sunk cost fallacy detector"
  - "identify sunk costs"
  - "decommission dead feature"
  - "kill doomed initiative"
  - "sunk cost evaluation"
  - "clean abandonment"
  - "cut past losses"
---

# Sunk Cost Fallacy Detector Skill

## Overview
Drawn from `cc-thinking-skills` and behavioral economics, this skill provides a rigorous cognitive audit to detect and overcome the Sunk Cost Fallacy in software development. Teams routinely pour weeks of additional engineering effort into doomed architectures, overly complex abstractions, or failing third-party integrations simply because "we already spent three months building it." This skill forces decision-makers to treat past expenditures (time, code lines, budget) as strictly unrecoverable and evaluate projects solely on prospective future return versus ongoing maintenance drag.

## Cognitive Audit Invariants
1. **Past Effort Is Irrelevant**: Code written in the past has zero bearing on whether that code should exist in the future. The only relevant metric is: "If we did not have this code today, would we build it right now?"
2. **Maintenance Drag Is Compounding**: Retaining a broken or underutilized subsystem is not "free"; it incurs ongoing cognitive load, test execution time, dependency upgrades, and security patching overhead.
3. **Decisive Decommissioning Over Endless Triage**: When a subsystem fails its value test, delete it completely. Do not leave dead code commented out or hidden behind dormant flags.

## Decommissioning Protocol
1. **Identify Sunk Cost Rationalizations**: Flag phrases like "we've invested too much to stop now", "let's try one more rewrite", or "it would be a waste of past work".
2. **Clean-Sheet Counterfactual**: Ask: "Starting from scratch today with our current knowledge, what is the simplest architecture that solves this problem?"
3. **Decommissioning Roadmap**: Formulate an atomic deprecation and deletion plan removing tests, config files, and dead code paths in a clean PR.
