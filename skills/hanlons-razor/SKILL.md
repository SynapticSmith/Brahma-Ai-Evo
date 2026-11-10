---
name: hanlons-razor
description: Diagnostic rule to distinguish deliberate breach or malice from benign misconfigurations, race conditions, and unhandled errors.
metadata:
  domain: cognitive
  tags: [thinking, reasoning, hanlons-razor, diagnostics, root-cause, error-analysis]
triggers:
  - "hanlons razor"
  - "distinguish malice from error"
  - "diagnose unexpected failure"
  - "benign misconfiguration"
  - "error attribution"
---

# Hanlon's Razor Skill

## Overview
Popularized by Robert J. Hanlon: *"Never attribute to malice that which is adequately explained by stupidity (or ignorance, misconfiguration, or complexity)."*

In software systems and AI operations, unexpected anomalies (e.g. dropped connections, rejected authentication tokens, corrupt state, missing files) are frequently assumed to be adversarial breaches, deliberate bypasses, or intentional regressions. Hanlon's Razor instructs engineers and agents to systematically evaluate benign systemic causes first before escalating to adversarial threat models.

## Diagnostic Hierarchy (Ranked by Probability)
1. **Timing & Race Conditions**: Asynchronous callbacks, socket closure before thread read, unawaited futures.
2. **Configuration & Environment**: Stale environment variables, missing paths, expired API keys, port collisions.
3. **Type / Schema Drift**: Unhandled `None`, renamed JSON field, integer vs string serialization mismatch.
4. **Platform / OS Divergence**: Windows path delimiters (`\`) vs POSIX (`/`), CRLF line endings, Store App permissions.
5. **Adversarial / Malicious Action**: Intentional prompt injection, token exfiltration, deliberate tampering.
