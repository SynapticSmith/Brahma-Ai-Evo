# Cookbook: Parametric OpenSCAD Part Modeling & STL Export

## Objective
Generate a parameterized, watertight OpenSCAD 3D solid model and compile it to an STL file.

## Prerequisites
- OpenSCAD CLI installed (`openscad.exe` in PATH).

## Execution Recipe
1. **Define Parametric Headers**:
   Declare all mechanical dimensions, tolerances, and mesh resolution:
   ```openscad
   $fn = 64; // Fragment resolution
   length = 50.0; width = 30.0; height = 15.0;
   wall_thick = 2.5; hole_diam = 4.2;
   ```
2. **Author Constructive Solid Geometry**:
   ```openscad
   module enclosure_base() {
     difference() {
       cube([length, width, height], center=true);
       translate([0, 0, wall_thick])
         cube([length - 2*wall_thick, width - 2*wall_thick, height], center=true);
       cylinder(d=hole_diam, h=height+2, center=true);
     }
   }
   enclosure_base();
   ```
3. **Compile Script to STL**:
   ```powershell
   openscad -o model.stl model.scad
   ```
4. **Verification Gate**:
   *Gate: OpenSCAD CLI exits code 0; generated STL file size > 0 and geometry is manifold.*
5. **Remediation**:
   If mesh contains non-manifold edges, add a small epsilon overlap (`0.01mm`) to CSG `difference()` cutouts.
