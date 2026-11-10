---
name: sop-builder
description: Standard Operating Procedure (SOP) and operational runbook compiler for repeatable human and agent workflows.
metadata:
  domain: devops
  tags: [sop, runbook, playbook, operations, procedure, workflows]
triggers:
  - "build an sop"
  - "create a runbook"
  - "operational procedure"
  - "sop builder"
  - "standard operating procedure"
  - "author a playbook"
---

# Standard Operating Procedure (SOP) Builder Skill

## Overview
Drawn from the `procedure-ops` framework, this skill standardizes repeatable operational procedures into structured runbooks. A well-constructed SOP ensures that critical procedures (deployments, disaster recovery, data migrations, security audits) can be executed identically by humans or AI agents without knowledge loss.

## SOP Anatomy
Every SOP must contain:
1. **Metadata Header**: Title, Version, Owner, Target Audience, Last Reviewed Date.
2. **Operational Scope**: Exact triggering conditions and target systems.
3. **Prerequisites & Credentials**: Required CLI tools, network access, environment variables, and role permissions.
4. **Step-by-Step Procedure**: Sequential numbered commands with expected outputs.
5. **Validation Checkpoints**: Sanity checks proving the step succeeded.
6. **Rollback & Abort Strategy**: Clear emergency instructions to restore previous state if any step fails.
7. **Post-Mortem Logging**: Audit trail instructions for compliance recording.
