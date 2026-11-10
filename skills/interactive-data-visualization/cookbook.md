# Cookbook: Declarative Interactive Vega-Lite Chart Generation

## Objective
Convert tabular data into an interactive, standalone HTML chart with zoom, pan, and hover tooltips.

## Prerequisites
- Tabular data file (`data.csv` or `data.json`).
- Python `altair` or pure Vega-Lite HTML template.

## Execution Recipe
1. **Prepare Data & Encoding Channels**:
   Map X (timestamp/category), Y (metric value), and Color (grouping).
2. **Author Self-Contained Vega-Lite Specification**:
   ```html
   <!DOCTYPE html>
   <html>
   <head>
     <script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
     <script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
     <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
   </head>
   <body>
     <div id="vis"></div>
     <script>
       const spec = {
         "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
         "description": "Interactive metric trends",
         "data": { "values": [ {"x": "2026-01", "y": 28}, {"x": "2026-02", "y": 55} ] },
         "mark": "bar",
         "encoding": {
           "x": {"field": "x", "type": "nominal", "axis": {"title": "Month"}},
           "y": {"field": "y", "type": "quantitative", "axis": {"title": "Latency (ms)"}},
           "tooltip": [{"field": "x"}, {"field": "y"}]
         }
       };
       vegaEmbed("#vis", spec);
     </script>
   </body>
   </html>
   ```
3. **Verification Gate**:
   *Gate: HTML file renders cleanly in browser; tooltips trigger on hover without console errors.*
4. **Remediation**:
   If axes clip text, add `"autosize": {"type": "fit", "contains": "padding"}` to spec.
