---
name: survivorship-bias-auditor
description: Cognitive auditing protocol evaluating missing datasets, silent failure populations, and unlogged edge cases in postmortems, benchmarks, and architectures.
metadata:
  domain: cognitive
  tags: [mental-model, survivorship-bias, postmortem, root-cause, statistics, telemetry, cognitive]
triggers:
  - "survivorship bias auditor"
  - "audit survivorship bias"
  - "identify missing failures"
  - "missing data in postmortem"
  - "survivor bias check"
  - "analyze silent failures"
  - "detect invisible errors"
---

# Survivorship Bias Auditor Skill

## Overview
Drawn from `cc-thinking-skills`, Abraham Wald's World War II aircraft armor analysis, and high-assurance telemetry engineering, this skill forces engineers and AI agents to examine the "missing dataset"—the cases that failed silently, crashed before emitting logs, or abandoned the system before reaching an observable checkpoint. When teams analyze only the requests that reached the error handler or only the users who submitted support tickets, they reinforce parts of the system that are already functioning while ignoring catastrophic blindspots where failures are completely invisible.

## The Abraham Wald Invariant
Wald observed that returning bombers had bullet holes clustered on the fuselage and wings, but none on the engines. Naive military leadership wanted to add armor where the bullet holes were; Wald realized armor belonged on the engines because planes shot in the engines never returned to be counted.

**Software Translation**: Code that produces logged exceptions is surviving long enough to be recorded. The most dangerous defects crash the process silently (`SIGSEGV`, OOM killer, unhandled thread panics, network socket drops before HTTP handshake) and leave zero trace in application logs.

## Audit Rules
1. **Search for What Is Not Logged**: In every incident investigation, ask: "What failure modes bypass our logging infrastructure completely?"
2. **Audit Process Exit Codes & OS OOM Events**: Do not rely on application-level error handlers; inspect OS system logs (`dmesg`, Windows Event Viewer) for kernel kills and hard process termination.
3. **Dropouts vs Completions**: In user or agent workflow funnels, measure dropout rate at every turn, not just explicit error clicks.

## Auditing Protocol
1. **Catalog Observed Cohort**: Review all visible logs, metrics, and passing tests.
2. **Brainstorm Invisible Casualties**: Identify scenarios where execution terminates before reaching the logging statement.
3. **Deploy Kernel & Process Sentinels**: Add external heartbeat monitors or watchdog supervisors outside the process boundary.
