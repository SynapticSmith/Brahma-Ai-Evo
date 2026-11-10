---
name: text-to-cad-modeling
description: Parametric 3D CAD modeling protocol converting natural language geometry specifications into programmatic OpenSCAD, CadQuery, or STEP/STL assets.
metadata:
  domain: coding
  tags: [cad, cae, cam, 3d-modeling, parametric, openscad, cadquery, stl, coding]
triggers:
  - "text to cad modeling"
  - "generate cad model"
  - "create 3d cad script"
  - "parametric 3d design"
  - "cad script generator"
  - "export stl model"
  - "build 3d part"
---

# Text-to-CAD Parametric Modeling Skill

## Overview
Drawn from `text-to-cad` and parametric engineering frameworks, this skill enables AI agents to author mathematically rigorous 3D geometry scripts (OpenSCAD, CadQuery, PythonOCC) from natural language descriptions. Non-parametric meshes (OBJ/PLY) suffer from non-manifold errors and cannot be reliably machined. This skill enforces programmatic constructive solid geometry (CSG), boundary representation (B-Rep), and parametric dimensional constraints.

## Modeling Invariants
1. **Parametric Dimensions at Top**: All lengths, radii, wall thicknesses, and tolerances must be defined as variables at the top of the script. No hardcoded magic numbers.
2. **Manifold Geometry Guarantee**: Ensure all solid bodies are 2-manifold (watertight, zero self-intersections, no zero-thickness walls).
3. **Manufacturability & Clearances**: Default to 0.2mm - 0.4mm clearances for interlocking 3D-printed joints; enforce draft angles for molded/machined parts.
4. **Standard Export Formats**: Scripts must be verifiable via CLI rendering and export to `.scad`, `.step`, or manifold `.stl`.

## Modeling Workflow
1. **Requirement Analysis**: Extract dimensions, symmetry axes, mounting holes, and mechanical tolerances.
2. **Primitive Scaffolding**: Assemble base solids (cubes, cylinders, spheres) using CSG union, difference, and intersection.
3. **Feature Refinement**: Apply fillets, chamfers, countersinks, and shell cutouts.
4. **CLI Render & Verification**: Render script via OpenSCAD/CadQuery CLI and inspect surface integrity.
