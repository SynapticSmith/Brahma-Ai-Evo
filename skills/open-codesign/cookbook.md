# Cookbook: Rapid UI Component Prototyping

## Objective
Generate a complete, accessible, and responsive frontend component prototype from a functional specification.

## Prerequisites
- UI requirement or wireframe brief.

## Execution Recipe
1. **Declare Semantic Design Tokens**:
   Setup base palette and spacing tokens in CSS `:root`:
   ```css
   :root {
       --primary: #3b82f6;
       --background: #0f172a;
       --surface: #1e293b;
       --text: #f8fafc;
       --radius: 8px;
   }
   ```
2. **Structure Semantic HTML**:
   Write clean markup using appropriate HTML5 tags (`<header>`, `<section>`, `<article>`).
3. **Apply Responsive CSS Grid/Flexbox**:
   Structure elements with auto-fit grids:
   ```css
   .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
   ```
4. **Style State Transitions**:
   Add subtle, performant transitions (`transform`, `opacity`) for interactive controls.
5. **Verification Gate**:
   - Check contrast ratio $\ge$ 4.5:1.
   - Verify keyboard navigability via `Tab` key.
   - Verify layout scales gracefully on 375px mobile view.
6. **Remediation**:
   If layout wraps awkwardly on small screens, adjust flex-wrap rules or decrease minimum column width.
