---
name: scroll-driven-web-design
description: Architecting and implementing scroll-driven web animations, interactive narrative timelines, CSS animation-timeline view(), and progressive scroll layout validation.
metadata:
  domain: coding
  tags: [frontend, scroll-animation, css, web-design, animation-timeline, interactive, coding]
triggers:
  - "scroll driven web design"
  - "scroll animations"
  - "css animation timeline"
  - "build scroll landing page"
  - "scroll narrative"
  - "scroll-driven experience"
---

# Scroll-Driven Web Design Skill

## Overview
Drawn from `scroll-craft`, this skill guides the creation of immersive, high-performance scroll-driven web interfaces where the scrollbar serves as the primary playback timeline. Traditional JavaScript scroll listeners cause layout thrashing and choppy framerates. This skill prioritizes native CSS scroll-driven animations (`animation-timeline: scroll()`, `view()`), hardware-accelerated transforms (`transform: translate3d`, `opacity`), and step-by-step layout floor verification.

## Core Design Principles
1. **Zero-Jank CSS Timelines**: Use native CSS `@keyframes` bound to `animation-timeline: view()` or `scroll()`. Avoid unthrottled JavaScript `window.onscroll` handlers.
2. **Transform & Opacity Only**: Animate strictly composited properties (`transform`, `opacity`) to ensure smooth 60fps rendering without triggering re-layouts or repaints.
3. **Progressive Enhancement**: Provide accessible fallbacks (`@supports not (animation-timeline: view())`) where elements remain fully visible and readable on legacy browsers.
4. **Visual Floor Calibration**: Divide the page into calibrated viewport height sections (`100vh` sticky pinning) to maintain pacing across screen sizes.

## Implementation Architecture
1. **Container Scaffolding**: Setup sticky pinning wrappers (`position: sticky; top: 0; height: 100vh;`) within tall scroll tracks (`height: 300vh;`).
2. **CSS Animation Timelines**: Define keyframes and bind with `view(block)` and `animation-range: entry 10% cover 90%`.
3. **Interactive Narrative Layers**: Layer typography, SVG diagrams, and device mockups that assemble and disassemble as the user scrolls.
4. **Layout Verification**: Test at standard viewport widths (mobile 375px, desktop 1440px) to prevent element overlapping.
