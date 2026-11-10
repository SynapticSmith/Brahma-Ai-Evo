# Cookbook: Structured SVG Canvas Wireframe Generation

## Objective
Author a semantic, scalable SVG visual mockup with header, metric cards, and layout grid.

## Prerequisites
- Standard SVG viewer or browser.

## Execution Recipe
1. **Define Root ViewBox & Design Tokens**:
   ```xml
   <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" width="100%" height="100%">
     <defs>
       <style>
         .bg { fill: #0f172a; }
         .card { fill: #1e293b; stroke: #334155; rx: 8px; }
         .text-title { fill: #f8fafc; font-family: system-ui; font-size: 20px; font-weight: 600; }
         .text-metric { fill: #38bdf8; font-family: system-ui; font-size: 32px; font-weight: 700; }
         .text-sub { fill: #94a3b8; font-family: system-ui; font-size: 14px; }
       </style>
     </defs>
     <rect width="1200" height="800" class="bg"/>
   ```
2. **Assemble Layout Grid & Components**:
   ```xml
     <g id="navbar">
       <rect x="40" y="30" width="1120" height="60" class="card"/>
       <text x="70" y="68" class="text-title">Brahma Canvas Dashboard</text>
     </g>
     <g id="metric-card-1">
       <rect x="40" y="120" width="350" height="160" class="card"/>
       <text x="70" y="160" class="text-sub">Total Active Nodes</text>
       <text x="70" y="210" class="text-metric">1,024</text>
     </g>
   </svg>
   ```
3. **Verification Gate**:
   *Gate: SVG renders valid XML with 0 parsing errors; all elements render inside viewBox.*
4. **Remediation**:
   If text overlaps rects, calculate vertical offset as `y = top + font_size + padding`.
