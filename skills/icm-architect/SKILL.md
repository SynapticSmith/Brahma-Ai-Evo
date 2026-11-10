---
name: icm-architect
description: Design and structure complex agent workflows and problem spaces as filesystem hierarchies using the Interpretable Context Methodology (ICM).
metadata:
  domain: applied-sciences/engineering-and-technology/software-engineering
  tags: [icm, architecture, filesystem, context, hierarchy, workflow]
triggers:
  - "design icm workspace"
  - "structure workspace"
  - "folder structure as architecture"
  - "context methodology"
---

# ICM Architect Skill

## Overview
Replaces monolithic, fragile agent prompts with transparent, auditable filesystem architectures. Offloads model cognitive burden onto structured directory trees so compact models can execute complex workflows reliably.

## Architectural Principles
1. **Filesystem as Cognitive Graph**: Directories represent stages, domains, or operational queues; files represent state, specifications, or immutable artifacts.
2. **Ephemeral Context Injection**: Never pollute long-term conversation transcripts with temporary reference material. Inject context dynamically before user turns.
3. **Append-Only Safety Queues**: Place new, unverified outputs in staged inbox directories (`_inbox/` or `staging/`) before promotion to canonical locations.
4. **Deterministic Routing**: Use token scoring, allowlist slugs, and strict boundary checks rather than open-ended model routing.

## Procedure
1. **Analyze the Problem Topology**:
   - Identify inputs (raw documents, user instructions, API feeds).
   - Identify processing stages (ingestion, transformation, validation, storage).
   - Identify outputs (canonical notes, verified code, reports).
2. **Scaffold Directory Layout**:
   - `input/` or `raw/`: Immutable source artifacts.
   - `staging/` or `_inbox/`: Generated candidates awaiting validation.
   - `canonical/` or `wiki/`: Verified, permanent knowledge or code assets.
   - `config/` or `_taxonomy/`: Machine-readable routing rules and indices.
3. **Define Transition Rules**:
   - Establish explicit entry and exit criteria for each directory transition.
   - Ensure every artifact carries structured frontmatter or manifest metadata.
