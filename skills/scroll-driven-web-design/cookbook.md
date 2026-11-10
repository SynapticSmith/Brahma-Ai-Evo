# Cookbook: Native CSS Scroll-Driven Animation & Layout

## Objective
Build a performant, 60fps scroll-driven landing page section using native CSS `animation-timeline`.

## Prerequisites
- HTML5 & modern CSS environment (Chrome 115+, Safari 17+ support).

## Execution Recipe
1. **Scaffold Sticky Timeline Section**:
   ```html
   <div class="scroll-track" style="height: 300vh; position: relative;">
     <div class="sticky-viewport" style="position: sticky; top: 0; height: 100vh; overflow: hidden;">
       <div class="hero-card">Interactive Narrative</div>
     </div>
   </div>
   ```
2. **Define Pure CSS Animation Timeline**:
   ```css
   @keyframes revealFade {
     from { opacity: 0; transform: translateY(40px) scale(0.95); }
     to { opacity: 1; transform: translateY(0) scale(1); }
   }
   .hero-card {
     animation: revealFade linear both;
     animation-timeline: view();
     animation-range: entry 15% cover 50%;
   }
   ```
3. **Add Accessible Fallback**:
   ```css
   @supports not (animation-timeline: view()) {
     .hero-card { opacity: 1; transform: none; }
   }
   ```
4. **Verification Gate**:
   *Gate: Animations run smoothly at 60fps; content is 100% visible if animation is disabled.*
5. **Remediation**:
   If layout jitters occur, replace animated `margin`/`top` with `transform: translate3d()`.
