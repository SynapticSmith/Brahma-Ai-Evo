# Agent Orchestration

Imported, distilled knowledge on coordinating multiple agents on one goal -- the topologies, the mechanics that keep them from drifting, and how to author each agent node so it composes.

This module is **not** part of ICM's 15 workspace conventions (those live in [`../patterns/`](../patterns/index.md)). It is external knowledge, reconciled with the hive's runtime.

## Pages

| Page | What it covers |
|------|----------------|
| [Agent Orchestration Patterns](agent-orchestration-patterns.md) | Topologies (pipeline, iterative, fork-join, producer-consumer, routing, hybrid), the scale ladder, coordination mechanics, and communication layers -- mapped to hive primitives |
| [Agent Definition Authoring](agent-definition-authoring.md) | How to write one subagent: trigger-shaped description, least-privilege tools, scope + output contract, anti-patterns, and a 4-step test |

## Source

Distilled from the `610ClaudeSubagents` corpus root guides (`agent-chaining-patterns.md`, `swarm-combinations.md`, `flowstrats.md`, `custom-agent-builder-guide.md`, `prompt-templates.md`, `tutorial.md`, `troubleshooting.md`). Structural patterns were carried over; marketing metrics were stripped and marked unverified. Catalog of the 610 source agents lives at `../../../hive/library/subagents/catalog.json`.

## Related

- [Patterns](../patterns/index.md) -- ICM's canonical 15 workspace conventions
- [Methodology](../methodology/index.md) -- context-routing theory behind "shared memory over chatter"
- [Templates](../templates/index.md) -- doc templates used to keep these pages consistent
