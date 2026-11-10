---
name: tradeoff-evaluator
description: Explicitly identify, quantify, and balance engineering trade-offs (CAP theorem, space-time, latency-throughput, safety-velocity).
metadata:
  domain: cognitive
  tags: [thinking, reasoning, tradeoffs, cap-theorem, architecture, compromises]
triggers:
  - "tradeoff analysis"
  - "evaluate trade-offs"
  - "cap theorem tradeoff"
  - "speed vs safety"
  - "space vs time"
---

# Tradeoff Evaluator Skill

## Overview
Based on economist Thomas Sowell's maxim: *"There are no solutions, there are only trade-offs."* In computing systems, every engineering victory in one dimension (e.g. latency, memory, consistency) incurs an intentional cost in another. This skill formalizes trade-off identification so compromises are deliberate, bounded, and monitored.

## Procedure
1. **Identify the Underlying Duality / Trilemma**:
   - Classify the active compromise:
     - *CAP Theorem*: Consistency vs. Availability under network partition.
     - *Space vs. Time*: Memory footprint vs. CPU computation cycles.
     - *Latency vs. Throughput*: Batching efficiency vs. real-time responsiveness.
     - *Safety vs. Friction*: Strict approval boundaries vs. autonomous agent velocity.
2. **Quantify the Gain and the Sacrifice**:
   - State explicitly: "By caching parsed ASTs in RAM, we gain 95% latency reduction (from 120ms to 6ms)."
   - State the sacrifice: "We sacrifice up to 45MB of RAM per worker process."
3. **Establish Invariant Guardrails on Sacrificed Properties**:
   - Define hard caps on the sacrificed dimension so it never spirals into system failure.
   - Example: LRU eviction capping cache RAM at 64MB; fallback to re-parsing if threshold is breached.
4. **Document Decision Record**:
   - Record the rationale, bounded trade-off, and telemetry alerts in the architecture log.
