---
name: zero-downtime-canary-deploy
description: High-availability canary deployment protocol enforcing progressive traffic splitting (1% -> 10% -> 50% -> 100%), error-budget monitoring, and automated rollbacks.
metadata:
  domain: devops
  tags: [canary, deployment, devops, traffic-routing, zero-downtime, metrics, automated-rollback]
triggers:
  - "zero downtime canary deploy"
  - "canary deployment"
  - "traffic split deploy"
  - "gradual rollout"
  - "canary analysis"
  - "automated canary rollback"
  - "progressive traffic rollout"
---

# Zero-Downtime Canary Deployment Skill

## Overview
Drawn from site reliability engineering (SRE) and progressive delivery practices, this skill governs the gradual rollout of new application versions to live production traffic. Deploying changes to 100% of users all at once exposes the entire user base to subtle regressions, latency spikes, or unhandled exceptions. This skill enforces a structured canary rollout: diverting a tiny fraction of live traffic (e.g. 2%), evaluating key health indicators against the baseline version, and rolling back automatically within seconds if error budgets are breached.

## Canary Rollout Rules
1. **Side-by-Side Baseline Comparison**: Run the canary instance alongside an identical baseline instance to cancel out ambient external traffic variations.
2. **Strict Metrics Verification**:
   - HTTP 5xx error rate must not exceed baseline by $> 0.1\%$.
   - P99 latency must not degrade by $> 15\%$.
3. **Automated Kill Switch**: If any health check fails or error rate breaches the safety threshold, immediately reroute 100% of traffic to the stable baseline without waiting for manual human approval.
4. **Stateless Schema Backward Compatibility**: Any database schema alterations must be backward-compatible with both canary and stable versions simultaneously (expand-contract pattern).

## Progressive Deployment Pipeline
1. **Canary Staging**: Deploy canary containers with health check probes.
2. **Phase 1 (2% Traffic)**: Route 2% of ingress traffic to canary; monitor metrics for 5 minutes.
3. **Phase 2 (20% Traffic)**: Increase traffic weighting to 20%; monitor for 10 minutes.
4. **Phase 3 (100% Promotion)**: Complete migration; terminate legacy baseline containers.
