---
name: chestertons-fence
description: Invariant rule requiring developers to discover and understand why legacy code or constraints exist before refactoring or removing them.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, chestertons-fence, refactoring, safety, legacy-code, invariants]
triggers:
  - "chestertons fence"
  - "why is this code here"
  - "before deleting this"
  - "refactoring caution"
  - "legacy code justification"
  - "understand before removing"
---

# Chesterton's Fence Skill

## Overview
Originating from philosopher G.K. Chesterton's 1929 parable: *"If you come across a fence erected across a road, do not tear it down until you understand why it was built in the first place."*

In software engineering, developers and AI agents frequently encounter seemingly redundant checks, odd timeouts, convoluted sanitizers, or legacy branches. Naively deleting them ("this looks useless") inevitably re-opens resolved production bugs, security breaches, or edge-case crashes.

## Core Rules
1. **Never Delete Without Provenance**: You may not remove a guard, check, or legacy module until you have found the git commit, issue, or bug report that introduced it.
2. **Reconstruct the Original Threat**: Identify the exact failure condition or platform quirk the code was intended to solve (e.g. Windows MSIX job object access violations, Gemini Live 1007 desynchronization).
3. **Validate Modern Relevance**: Check if the underlying condition still holds true today.
4. **Author a Test Before Alteration**: Write a regression test verifying the edge case before altering or retiring the fence.
