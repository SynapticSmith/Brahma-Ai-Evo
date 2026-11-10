---
name: interactive-data-visualization
description: Interactive exploratory data analysis and declarative visualization protocol converting tabular data into responsive Vega-Lite, ECharts, or static charts.
metadata:
  domain: coding
  tags: [data-visualization, charts, vega-lite, echarts, matplotlib, analytics, exploratory-analysis, coding]
triggers:
  - "interactive data visualization"
  - "generate interactive chart"
  - "create vega lite chart"
  - "plot data chart"
  - "interactive dashboard plot"
  - "visualize tabular data"
  - "render data visualization"
---

# Interactive Data Visualization Skill

## Overview
Drawn from `data-formulator` and declarative visualization grammars, this skill governs the synthesis of publication-quality interactive and static charts from structured tabular datasets (CSV, JSON, SQL queries). Ad-hoc plotting often produces misleading axes, missing units, unreadable labels, or unscaled color palettes. This skill enforces strict visual grammar rules (Wilkinson's Grammar of Graphics): mapping dimensions to appropriate visual channels, ensuring color accessibility, providing interactive tooltips, and rendering standalone HTML embeds.

## Visual Encoding Rules
1. **Zero Unlabeled Axes**: Every axis must have an explicit label, unit of measurement, and legible tick intervals.
2. **Channel Appropriateness**:
   - Categorical dimensions $\rightarrow$ Hue or spatial clustering (never sequential brightness).
   - Quantitative dimensions $\rightarrow$ Position along a common scale (bar, dot) or length (never angle or 3D volume).
   - Time series $\rightarrow$ Continuous horizontal X-axis with uniform date formatting.
3. **Zero 3D Charts**: Prohibit 3D pie charts, 3D bar charts, or perspective distortions that distort quantitative perception.
4. **Standalone Portability**: Visualizations intended for reports must embed their data inline or load from self-contained JSON/HTML.

## Visualization Workflow
1. **Data Profile**: Inspect column data types (nominal, ordinal, quantitative, temporal), distributions, and null values.
2. **Chart Selection**: Choose optimal idiom: Bar (comparisons), Line (trends), Scatter (correlations), Heatmap (matrix density).
3. **Declarative Specification**: Author Vega-Lite JSON or ECharts config mapping encodings to data fields.
4. **Verification & Polish**: Validate responsive resizing, tooltip format, and color contrast.
