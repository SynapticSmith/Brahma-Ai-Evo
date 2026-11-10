---
name: loss-aversion-calibrator
description: Cognitive decision-making framework neutralizing asymmetric loss aversion to evaluate architectural refactorings and technological migrations objectively.
metadata:
  domain: cognitive
  tags: [mental-model, loss-aversion, decision-theory, cognitive, risk-management, strategy]
triggers:
  - "loss aversion calibrator"
  - "calibrate loss aversion"
  - "overcome fear of change"
  - "asymmetric risk evaluation"
  - "loss aversion audit"
  - "bold refactoring decision"
  - "evaluate change risk"
---

# Loss Aversion Calibrator Skill

## Overview
Drawn from `cc-thinking-skills`, Kahneman & Tversky's Prospect Theory, and strategic risk management, this skill counters the pervasive human cognitive bias where the psychological pain of losing something (e.g. breaking a working build, introducing a temporary bug) feels roughly twice as intense as the joy of gaining an equivalent benefit (e.g. 5x faster test runs, modern clean architecture). This bias produces chronic architectural stagnation: developers tolerate terrible, brittle codebases because "at least it currently works," fearing any change. This skill establishes symmetric expected value (EV) calculations to calibrate risk objectively.

## Cognitive Invariants
1. **Status Quo Is an Active Choice**: Staying with the current architecture is not "free" or "neutral"; it is an active decision to incur technical debt, slower development velocity, and maintenance attrition.
2. **Quantify Expected Value Symmetrically**:
   $$EV = (P_{gain} \times V_{gain}) - (P_{loss} \times C_{loss})$$
   Never weigh losses at $2\times$ without explicit mathematical justification.
3. **De-Risk via Reversible Doors**: Turn high-stakes irreversible losses into low-stakes reversible experiments (Type 2 decisions) via feature flags, canary branches, or dual-run shadow pipelines.

## Calibration Workflow
1. **Identify Loss Fear Signatures**: Flag arguments motivated by dread of change ("if it ain't broke don't fix it", "we might break something subtle").
2. **Calculate Baseline Drag**: Calculate the real cost of inaction (hours wasted debugging, test run delays, security liabilities).
3. **Establish Downside Bounds**: Define the absolute worst-case scenario and design explicit mitigations (e.g. instant git rollback).
4. **Decide on Expected Net Value**: Authorize refactoring whenever expected net value is strictly positive.
