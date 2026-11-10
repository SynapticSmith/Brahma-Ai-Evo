---
name: pre-mortem-analysis
description: Prospective hindsight technique to identify root vulnerabilities, state desync, and failure modes before executing irreversible operations.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, pre-mortem, risk-analysis, prospective-hindsight]
triggers:
  - "pre-mortem"
  - "premortem analysis"
  - "prospective hindsight"
  - "what could go wrong"
  - "failure risk assessment"
---

# Pre-Mortem Analysis Skill

## Overview
Based on cognitive psychologist Gary Klein's prospective hindsight method. Before deploying, refactoring, or running an irreversible operation, assume the system has already failed catastrophically in production. Work backwards from the wreckage to uncover root risks and install defenses.

## Procedure
1. **Prospective Failure Framing**:
   - Project yourself forward in time (e.g., 2 weeks post-deployment).
   - Establish the ground assumption: "The release crashed catastrophically; the data is corrupt and users are locked out."
2. **Exhaustive Vulnerability Brainstorm**:
   - Independently generate all plausible failure vectors across four primary seams:
     - *State synchronization*: race conditions, stale cache reads, uncommitted transactions.
     - *Resource exhaustion*: memory leaks, thread pool starvation, file handle exhaustion.
     - *Schema mismatch*: unmigrated database columns, changed API response types, missing fields.
     - *Permission & Network*: expired credentials, firewall blocks, token timeouts.
3. **Likelihood & Impact Prioritization**:
   - Score each identified vector on Likelihood (1-5) and Severity (1-5).
   - Focus immediate engineering defenses on vectors scoring >= 15.
4. **Pre-Emptive Immunization**:
   - Implement rollback procedures, healthchecks, transaction boundaries, and automated alerts for the top threats.
