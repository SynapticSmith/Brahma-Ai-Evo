# Cookbook: Compiling Self-Contained HTML Reports

## Objective
Generate a complete, single-file interactive HTML dashboard answering a complex user query or presenting system metrics.

## Prerequisites
- Structured data, metrics, or comparison table to visualize.

## Execution Recipe
1. **Initialize Self-Contained Template**:
   Start with standard responsive HTML5 skeleton with inline system-font CSS.
2. **Embed Key Metric Cards**:
   Create a top-level grid of summary metric cards (Totals, Success Rates, Latency).
3. **Format Data as Interactive Table**:
   Render rows inside `<table id="report-table">`. Include a quick search filter:
   ```html
   <input type="text" id="filter" placeholder="Filter rows..." onkeyup="filterTable()">
   ```
4. **Embed Pure CSS / Canvas Visuals**:
   Use CSS bar charts or SVG sparklines for trend comparisons.
5. **Write File to Disk**:
   Save to target path (e.g. `reports/<topic>-dashboard.html`).
6. **Verification Gate**:
   Open in browser or verify file syntax:
   - File opens completely offline with 0 broken asset requests.
   - Total file size $\le$ 200 KB.
7. **Remediation**:
   If icons or external fonts break, replace with inline SVGs and system font stack (`system-ui, -apple-system, sans-serif`).
