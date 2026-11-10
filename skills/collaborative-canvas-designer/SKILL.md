---
name: collaborative-canvas-designer
description: Declarative visual UI/UX wireframing and multiplayer canvas authoring protocol generating structured SVG canvas mockups, layout boards, and design tokens.
metadata:
  domain: coding
  tags: [design, canvas, wireframe, svg, whiteboard, ui-design, design-system, coding]
triggers:
  - "collaborative canvas designer"
  - "generate canvas wireframe"
  - "author visual mockup"
  - "whiteboard diagram canvas"
  - "interactive ui canvas"
  - "design canvas mockup"
  - "canvas layout board"
---

# Collaborative Canvas Designer Skill

## Overview
Drawn from `doop` and infinite design canvas architectures, this skill enables AI agents to author structured, resolution-independent 2D visual layouts, whiteboard wireframes, and UI mockups. Traditional design tools lock layout intent inside opaque binary files (Figma, Sketch). This skill generates pure, semantic SVG documents, JSON canvas states, and CSS design tokens that humans and AI agents can co-edit and inspect programmatically.

## Canvas Invariants
1. **Semantic Vector Hierarchy**: All canvas elements must be organized into logical SVG groups (`<g id="header">`, `<g id="sidebar">`, `<g id="content">`) with explicit semantic identifiers.
2. **Design System Token Alignment**: Spacing, typography, border radii, and color palettes must strictly derive from a cohesive token set (e.g. 4px/8px spatial grid, neutral gray scale, primary accent).
3. **Responsive ViewBox Definition**: Always declare `viewBox="0 0 W H"` with vector scaling properties to ensure legible rendering across 4K displays and mobile previews.
4. **Accessible Typography**: Use standard web-safe system font stacks (`system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif`) with high-contrast text ratios ($\ge 4.5:1$).

## Canvas Design Workflow
1. **Information Architecture**: Define component hierarchy, information density, and screen layout zones.
2. **Grid & Frame Scaffolding**: Setup root SVG viewBox, layout columns, and background card containers.
3. **Component Synthesis**: Render navigation bars, interactive buttons, metric cards, and data tables.
4. **Annotation & Connectors**: Add flow arrows and state markers explaining user interactions.
