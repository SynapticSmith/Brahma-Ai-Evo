---
name: answer-me-with-html
description: Generate standalone, self-contained single-page responsive HTML dashboards and reports with interactive styling and zero external dependencies.
metadata:
  domain: coding
  tags: [html, visualization, dashboard, report, single-page, self-contained]
triggers:
  - "answer me with html"
  - "generate html report"
  - "single page dashboard"
  - "interactive html answer"
  - "visual html summary"
  - "render html page"
---

# Answer Me with HTML Skill

## Overview
Drawn from the `answer-me-with-html` repository, this skill transforms complex, multi-variable analytical answers into beautiful, self-contained single-page HTML files. Instead of forcing the user to read 500 lines of markdown text, the agent compiles a complete interactive document containing structured tables, metric cards, dark/light mode toggles, and pure CSS/vanilla JS charts.

## Core Rules
1. **Zero External CDN Dependencies**: The generated HTML must be 100% self-contained. Do not link external CDNs (like unpkg or cdnjs) that fail when offline.
2. **Embedded CSS & JS**: All styles (`<style>`) and logic (`<script>`) must be inline.
3. **Responsive & Accessible**: Support dark mode via `prefers-color-scheme`, clean typography (system font stack), and mobile viewport tags.
4. **Interactive Filters**: Include client-side search/filter boxes for large data tables.
