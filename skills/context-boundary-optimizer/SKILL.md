---
name: context-boundary-optimizer
description: Optimize agent context windows via Interpretable Context Methodology (ICM), token budgeting, and just-in-time progressive disclosure.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, context-optimization, token-budget, icm, prompt-engineering]
triggers:
  - "context boundary"
  - "optimize context"
  - "token budget minimization"
  - "progressive disclosure"
  - "jit context injection"
---

# Context Boundary Optimizer Skill

## Overview
Based on the Interpretable Context Methodology (ICM). An LLM's context window is not an infinite garbage bin; it is a scarce, high-cost L1 cache. Dumping unindexed documents, raw code dumps, and redundant schemas degrades attention mechanisms, triggers hallucinations, and inflates cost/latency. This skill structures context injection into four progressive disclosure tiers (L0 resident index -> L1 shortlisted metadata -> L2 targeted SOP/cookbook -> L3 full reference) to minimize token footprint while maximizing execution precision.

## Procedure
1. **Context Breakdown & Audit**:
   - Decompose prompt into components: System prompt, Tool declarations, Short-term conversation history, Working memory, and Injected context.
   - Measure token count of each component.
2. **Apply Progressive Disclosure Tiers**:
   - *L0 Resident*: Restrict to high-level category map (~300 tokens). Never dump full catalog into base prompt.
   - *L1 Shortlist*: Query intent and shortlist top 3-5 candidates with compact triggers.
   - *L2 Just-In-Time Injection*: Inject only the specific 1-2 page executable cookbook for the immediate turn (< 1,500 tokens).
   - *L3 Deferred Reference*: Leave long documentation on disk, accessed via explicit search or read tools.
3. **Prune Redundancy & Clutter**:
   - Strip verbose Markdown fluff, repetitive disclaimer banners, and duplicated parameter descriptions.
   - Enforce character-to-token ratio budgets (`len(prompt) // 4 <= target_budget`).
4. **Enforce Turn Context Isolation**:
   - Clear ephemeral turn-specific SOP injections once the turn completes so subsequent turns remain uncluttered.
