---
name: incident-response
description: Structured emergency incident response protocol to gather logs, assess blast radius, confirm scope, apply mitigations, verify recovery, and author postmortems.
metadata:
  domain: devops
  tags: [incident, ops, outage, emergency, postmortem, mitigation, recovery]
triggers:
  - "incident response"
  - "production is down"
  - "service is broken"
  - "handle outage"
  - "emergency mitigation"
  - "author postmortem"
  - "respond to incident"
---

# Incident Response Playbook Skill

## Overview
Drawn from `OpenMonoAgent.ai`'s incident-response playbook, this skill guides agents and on-call engineers through a calm, disciplined emergency mitigation sequence. When critical services break or fail, panic leads to destructive hasty edits that exacerbate downtime. This skill enforces strict procedural gating: evidence must be gathered before acting, scope must be confirmed before mitigating, and recovery must be verified before declaring resolution.

## Immutable Safety Constraints
1. **Never Restart or Rollback Blindly**: Never restart a service, apply a rollback, or modify production infrastructure before gathering error logs and identifying the blast radius.
2. **Never Expose Secrets in Postmortems**: Strip API keys, session tokens, passwords, and PII from error traces.
3. **Verify Recovery Before Closing**: Always confirm health checks, metrics, and latency return to baseline before marking the incident resolved.
4. **Author Permanent Postmortem**: The incident is not closed until a structured postmortem document is written to disk covering the timeline, root cause, mitigation steps, and preventative action items.
