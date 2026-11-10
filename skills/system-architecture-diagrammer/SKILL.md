---
name: system-architecture-diagrammer
description: Generate clean, readable system architecture diagrams, state machines, and component maps using Mermaid and pure SVG.
metadata:
  domain: architecture
  tags: [diagrams, mermaid, architecture, svg, visualization, dataflow]
triggers:
  - "draw architecture diagram"
  - "system diagram"
  - "generate mermaid chart"
  - "component flowchart"
  - "visualize architecture"
  - "state machine diagram"
---

# System Architecture Diagrammer Skill

## Overview
Drawn from the `anatomy` framework, this skill proceduralizes the creation of technical diagrams. It converts complex architectural abstractions (IPC boundaries, tool bridges, message buses, state transitions) into precise Mermaid or SVG diagrams that explain structural relationships at a glance.

## Supported Diagram Types & Invariants
1. **Flowcharts (`flowchart TD` / `flowchart LR`)**: For data pipelines, execution stages, and supervisor-worker trees.
2. **Sequence Diagrams (`sequenceDiagram`)**: For async network protocols, tool calls, and WebSocket lifecycles.
3. **State Diagrams (`stateDiagram-v2`)**: For UI states, agent lifecycle modes (STANDBY, LISTENING, THINKING, EXECUTING).
4. **Entity-Relationship (`erDiagram`)**: For database schemas and taxonomy hierarchies.

## Syntax Invariants
- Always quote labels containing brackets, parentheses, or colons: `id["Step (Details)"]`.
- Never use unsupported HTML tags inside node labels.
- Subgraphs must have descriptive labels and distinct variable names.
