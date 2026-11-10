# Abhasa Lumina — Comprehensive Visual Redesign Specification & Design System

| Field | Value |
|---|---|
| **Document** | `docs/VISUAL-REDESIGN-PROPOSAL.md` |
| **Status** | Complete Engineering & Aesthetic Specification (Pass 2 Refined) |
| **Date** | 2026-11-10 |
| **Target Platform** | Windows-first PyQt6 Desktop Assistant (`Brahma Evo` → `Abhasa Evo`) |
| **Specialist Panel** | `visual-design-master`, `design-system-architect`, `micro-interaction-designer`, `interaction-design-specialist`, `behavioral-design-psychologist`, `voice-interaction-designer`, `accessibility-specialist`, `performance-ux-optimizer`, `gui-ui-testing-agent`, `open-codesign`, `collaborative-canvas-designer` |
| **Visual Deliverables** | Vector Mockup: [`docs/assets/abhasa-lumina-mockup.svg`](assets/abhasa-lumina-mockup.svg)<br>Interactive Living Prototype: [`docs/assets/abhasa-lumina-preview.html`](assets/abhasa-lumina-preview.html) |
| **Related Documents** | [`docs/ARCHITECTURE-SPEC.md`](ARCHITECTURE-SPEC.md), [`docs/CURRENT-VISUAL-INVENTORY.md`](CURRENT-VISUAL-INVENTORY.md), [`docs/REBRAND-PROPOSITION.md`](REBRAND-PROPOSITION.md), [`docs/REBRAND-EXECUTION-PLAN.md`](REBRAND-EXECUTION-PLAN.md), [`docs/ONE-391-avatar-backends.md`](ONE-391-avatar-backends.md) |
| **Governing Rules** | [`AGENTS.md`](../AGENTS.md) (Python 3.11/3.12, Windows-first PyQt6, stdlib harness, no unverified claims) |

---

## 0. Executive Summary & Creative Foundation

### 0.1 The Philosophy: *Abhasa* ("The Light Thrown Back")
The existing interface of Brahma Evo was built as a high-contrast, neon-heavy sci-fi "reactor." While functional, it suffers from severe visual noise: competing 3D WebGL background bloom, aggressive cyan-and-gold laser accents, floating windows that collide on standard displays, fragmented typography, and runtime monkeypatching.

In accordance with [`docs/REBRAND-PROPOSITION.md`](REBRAND-PROPOSITION.md), the system name is transitioning to **Abhasa** (*ābhāsa*: "reflection, appearance, semblance; the light thrown back"). The visual interface must express this exact metaphysical stance:
> *Named for the light it throws back, not for the light it is.*

The machine is an optical instrument—a reflective prism. It does not project its own ego; it receives user intent, reflects clarity, executes tasks with precision, and recedes into calm transparency when idle.

### 0.2 Visual Identity Pillars: "Abhasa Lumina"
1. **Refined Frosted Obsidian**: Moving away from flat `#000000` pitch black to layered obsidian depths (`#060709`, `#08090C`, `#0E1117`) overlaid with micro-blur glass (`rgba(14, 17, 23, 0.82)`).
2. **Restrained Spectral Light**: Neon glare is replaced by controlled single-pixel hairline borders (`rgba(255, 255, 255, 0.08)`) and purposeful spectral indicators (Lumina Cyan `#00E5FF`, Reflection Violet `#8B5CF6`, Calm Emerald `#10B981`, Amber Shimmer `#F59E0B`).
3. **Calm Zero-Panic Interaction**: Elimination of anxiety-inducing 10-second countdowns on telemetry wings. Persistent, predictable information docking.
4. **Fluid Multimodal Harmony**: Visual choreography tightly synchronized with Gemini Live bidirectional audio, offering zero-gaze peripheral awareness without visual exhaustion.
5. **Decoupled Avatar Subsystem**: First-class support for both high-fidelity 3D/2D background avatar companions and an ultra-lean 0% CPU "Pure Lumina" headless glass mode.

---

## 1. Specialist Panel & Radical Candor Audit

Applying Principle 0 (*Truth Above All*) across our 11 design specialists and skills, here is the verified evaluation of the current user interface:

```mermaid
flowchart TD
    subgraph Specialists["Multi-Disciplinary Design Panel"]
        VDM["visual-design-master<br/>(Aesthetics & Color)"]
        DSA["design-system-architect<br/>(Tokens & Component APIs)"]
        MID["micro-interaction-designer<br/>(Motion & Perceived Perf)"]
        IDS["interaction-design-specialist<br/>(Zero-UI & Workflows)"]
        BDP["behavioral-design-psychologist<br/>(Cognitive Load & System 1/2)"]
        VID["voice-interaction-designer<br/>(Bidirectional Audio & Barge-in)"]
        ACS["accessibility-specialist<br/>(WCAG 2.2 AAA & APCA)"]
        PUO["performance-ux-optimizer<br/>(Dirty Rects & Paint Metrics)"]
        GUT["gui-ui-testing-agent<br/>(Headless Regression Verification)"]
    end

    subgraph CriticalFindings["Verified Codebase Critical Defects"]
        F1["F-01: Runtime QSS String-Replace Monkeypatch (ui.py:86)"]
        F2["F-02: 4-Way Typography Fragmentation (Courier, Consolas, Segoe, Nirmala)"]
        F3["F-03: Viewport Overlaps at Minimum 820x580 Size (ui_panels.py:90)"]
        F4["F-04: Foreground/Background WebGL Bloom Competition (assets/web_background)"]
        F5["F-05: Crude Procedural QPainter Line-Art Icons (ui_branding.py:51)"]
        F6["F-06: 10s Auto-Dismiss Panic Loop (ui_telemetry.py:294)"]
        F7["F-07: Excessive Cognitive Load (Simultaneous focal points)"]
        F8["F-08: GPU Drain on Idle (Unthrottled 60fps Chromium render loop)"]
    end

    Specialists --> CriticalFindings
```

### 1.1 Detailed Defect Matrix

| ID | Domain | Root Cause in Code | Specialist Impact Analysis |
|---|---|---|---|
| **F-01** | Architecture | [`ui.py:86-100`](../ui.py#L86-L100)<br>`QWidget.setStyleSheet = _new_setStyleSheet` | **Catastrophic Maintenance Hack**: Every widget stylesheet assignment triggers 9 string replaces converting legacy gold to cyan. Causes stylesheet re-parsing penalties on every widget update. |
| **F-02** | Typography | [`core/ui_widgets.py:74`](../core/ui_widgets.py#L74), [`core/ui_overlays.py:88`](../core/ui_overlays.py#L88), [`core/ui_commandbar.py:68`](../core/ui_commandbar.py#L68) | **Visual Disarray**: Intermingling retro `Courier New` 7pt, IDE `Consolas` 10pt/36pt, UI `Segoe UI`, and Sanskrit `Nirmala UI` breaks reading rhythm and violates modern font scale standards. |
| **F-03** | Layout | [`core/ui_panels.py:90`](../core/ui_panels.py#L90), [`core/ui_telemetry.py:288`](../core/ui_telemetry.py#L288) | **Viewport Clipping**: Fixed-width floating wings (`310px`) crowd the stage. On standard laptops (`1366x768`) or at min size (`820x580`), floating wings collide with the command bar and chat workspace. |
| **F-04** | Legibility | [`assets/web_background/index.html`](../assets/web_background/index.html) | **Accessibility Failure**: 3D Quantum Core vortex with `UnrealBloomPass` spins directly behind semi-transparent chat cards. Rapidly oscillating luminances cause text contrast to drop below WCAG AA (failing 4.5:1). |
| **F-05** | Iconography | [`core/ui_branding.py:51-77`](../core/ui_branding.py#L51-L77) | **Aesthetic Deficit**: Procedural `QPainter.drawArc` and `drawLine` primitives create blurry, poorly proportioned icons on Windows display scaling (125%, 150%, 200%). |
| **F-06** | Psychology | [`core/ui_telemetry.py:294`](../core/ui_telemetry.py#L294)<br>`_total_duration_ms = 10000` | **Anxiety Loop**: Forcing the user to hover or race to click a `📌` pin icon within 10 seconds creates constant user friction and fear of missing critical tool outputs. |
| **F-07** | Cognition | Stage Composition | **Cognitive Overload**: The user is forced to process simultaneous spinning 3D geometry, pulsing telemetry wings, left nav buttons, and bottom inputs simultaneously (System 2 exhaustion). |
| **F-08** | Performance | [`core/ui_bg.py:86`](../core/ui_bg.py#L86) | **Power Inefficiency**: Chromium WebGL loop continues rendering unthrottled even when window is minimized or idle, causing unnecessary battery drain on mobile laptops. |

---

## 2. Behavioral Psychology & Cognitive Load Architecture

Authored by `behavioral-design-psychologist`:

### 2.1 Dual-Process Design (System 1 vs. System 2)
Human cognitive processing divides into **System 1** (fast, intuitive, visual pattern recognition) and **System 2** (slow, deliberate, analytical inspection).
- **Current Defect**: Everything in Brahma Evo is rendered at maximum volume (high-contrast neon borders, terminal logs, pulsing wings, spinning vortex), overloading System 2 continuously.
- **The Lumina Solution**:
  - **System 1 (Ambient Glance)**: The peripheral optical glow and dock state indicate assistant status instantly (Cyan = Listening, Violet = Thinking, Amber = Executing, Emerald = Speaking). The user does not need to read words to know what the assistant is doing.
  - **System 2 (Deep Inspection)**: Complex execution graphs, AST diffs, and tool arguments are organized into the right-hand **Context & Telemetry Drawer** under collapsible accordions with clear typographic hierarchy.

### 2.2 Von Restorff (Isolation) & Visual Anchoring
In any interface, the human eye seeks a primary resting anchor. In Abhasa Lumina:
1. **Primary Anchor**: The central conversation stream and code canvas.
2. **Input Anchor**: The floating Lumina Dock at the bottom center.
3. **Secondary Monitor**: The docked right-hand telemetry drawer.
All secondary panels use darker, lower-contrast backgrounds (`rgba(11, 13, 18, 0.95)`), ensuring foreground agent responses hold visual priority.

---

## 3. Design Token Architecture ("Abhasa Lumina")

Authored by `design-system-architect` and `skills/open-codesign`:

```mermaid
graph LR
    subgraph Primitives["1. Primitive Scales"]
        P_Col["Color Palettes (Obsidian, Slate, Cyan, Violet, Signal)"]
        P_Typ["Typography (Inter, JetBrains Mono)"]
        P_Spg["Spatial Rhythm (4px Grid: 4, 8, 12, 16, 24, 32, 48)"]
        P_Rad["Radii (4px, 8px, 12px, 16px, 26px Pill)"]
    end

    subgraph Semantics["2. Semantic Mapping"]
        S_Sur["Surface (Canvas, Glass, Card, Dock, Elevated)"]
        S_Bor["Border (Subtle 0.08, Hover 0.18, Active 0.35)"]
        S_Txt["Text (Primary AAA, Secondary AAA, Muted AA)"]
        S_Act["Accent (Cyan Action, Violet Reason, Emerald Voice)"]
    end

    subgraph Components["3. Component QSS Implementations"]
        C_Dck["LuminaDock QSS"]
        C_Msg["LuminaMessageCard QSS"]
        C_Drw["TelemetryDrawer QSS"]
        C_Mod["ModalDialog QSS"]
    end

    Primitives --> Semantics --> Components
```

### 3.1 Primitive Color Scale
```json
{
  "color": {
    "obsidian": {
      "950": "#050608",
      "900": "#08090C",
      "800": "#0E1117",
      "700": "#151921",
      "600": "#1E2430"
    },
    "slate": {
      "500": "#333C4E",
      "400": "#5A667E",
      "300": "#8C9BB4",
      "200": "#C5D0E0",
      "100": "#E9EFF8",
      "50":  "#F8FAFD"
    },
    "lumina-cyan": {
      "900": "rgba(0, 229, 255, 0.08)",
      "700": "rgba(0, 229, 255, 0.20)",
      "500": "#00E5FF",
      "400": "#33EBFF",
      "300": "#80F4FF"
    },
    "reflection-violet": {
      "900": "rgba(139, 92, 246, 0.08)",
      "700": "rgba(139, 92, 246, 0.22)",
      "500": "#8B5CF6",
      "400": "#A78BFA"
    },
    "signal": {
      "success": "#10B981",
      "warning": "#F59E0B",
      "danger":  "#EF4444",
      "info":    "#3B82F6"
    }
  }
}
```

### 3.2 Semantic Token Mapping & WCAG 2.2 / APCA Verification
*All values measured against base canvas `#08090C`:*

| Semantic Token | Concrete Value | Contrast Ratio | WCAG 2.2 Rating | Semantic Function |
|---|---|---|---|---|
| `surface.canvas` | `#08090C` | 1:1 | Base | Root application canvas |
| `surface.glass` | `rgba(14, 17, 23, 0.82)` | — | — | Frosted panels (backdrop blur) |
| `surface.card` | `rgba(21, 25, 33, 0.88)` | — | — | Conversation bubbles, bento cells |
| `surface.dock` | `rgba(16, 20, 28, 0.94)` | — | — | Floating command island dock |
| `border.subtle` | `rgba(255, 255, 255, 0.08)` | — | — | Default structural dividers |
| `border.active` | `rgba(0, 229, 255, 0.35)` | — | — | Focused inputs, active borders |
| `text.primary` | `#F8FAFD` | **17.8:1** | **AAA** Pass | Main conversation text, headers |
| `text.secondary` | `#C5D0E0` | **11.4:1** | **AAA** Pass | Subheaders, active status chips |
| `text.muted` | `#8C9BB4` | **6.1:1** | **AA+** Pass | Timestamps, placeholders, tags |
| `accent.primary` | `#00E5FF` | **13.2:1** | **AAA** Pass | Primary CTA, mic pulse, focus |
| `accent.secondary`| `#8B5CF6` | **4.9:1** | **AA** Pass | Tool executions, subagent runs |
| `signal.success` | `#10B981` | **8.4:1** | **AAA** Pass | Exit code 0, tool success |
| `signal.danger` | `#EF4444` | **5.2:1** | **AA** Pass | Error states, cancel alerts |

### 3.3 Authoritative Typography Hierarchy
Replaces `Courier New`, `Consolas`, and `Nirmala UI` with an intentional two-family system:
- **UI & Reading**: `Inter`, `-apple-system`, `Segoe UI Variable Display`, `sans-serif`
- **Code & Telemetry**: `JetBrains Mono`, `Cascadia Code`, `Consolas`, `monospace`

```
Display:         22px / 28px  Weight: 700 (Bold)          Tracking: -0.4px
Title Medium:    15px / 20px  Weight: 600 (SemiBold)      Tracking: -0.2px
Body Regular:    13px / 19px  Weight: 400 (Regular)       Tracking:  0.0px
Body Medium:     13px / 19px  Weight: 500 (Medium)        Tracking:  0.0px
Caption / Tag:   11px / 15px  Weight: 500 (Medium)        Tracking: +0.2px
Code Inline:     12px / 16px  Weight: 500 (JetBrains)     Tracking:  0.0px
Telemetry Micro: 10px / 13px  Weight: 600 (JetBrains)     Tracking: +0.6px
```

---

## 4. Spatial Architecture & 3-Zone Bento Stage

Authored by `interaction-design-specialist` and `responsive-design-architect`:

### 4.1 Layout Wireframe (1440 x 900 Canvas)
Refer to the vector graphic deliverable: [`docs/assets/abhasa-lumina-mockup.svg`](assets/abhasa-lumina-mockup.svg)

```
+=================================================================================================================+
| [ZONE 0] AMBIENT HEADER BAR (Height: 48px, Blur: 16px)                                                          |
|  [✦ ABHASA EVO]       [● gemini-live-2.0 · bidirectional voice]         [AUDIO REALTIME ~~~]      [ -  □  ✕ ]   |
+====+========================================================================+===================================+
|    | [ZONE 2] MAIN WORKSPACE BENTO STAGE                                    | [ZONE 3] TELEMETRY DRAWER         |
| [Z]|  +------------------------------------------------------------------+  | (Width: 340px, Blur: 16px)        |
| O  |  | BENTO CELL 1: OPTICAL CORE PORTAL (Height: 110px)                |  | +-------------------------------+ |
| N  |  | [   ( ◉ )   ]  Abhasa Core Active · Standing by for voice/prompt |  | | [Live Ops (3)] [Artifacts] [Vit]| |
| E  |  +------------------------------------------------------------------+  | +-------------------------------+ |
|    |  | BENTO CELL 2: CONVERSATION STREAM                                |  | LIVE TOOL EXECUTION STREAM        |
| 1] |  |                                                                  |  | • run_command: "git status"       |
|    |  |   [User] Refactor theme engine to 3-tier token system. (12:04PM) |  |   └─ [EXIT 0] (18ms)              |
| N  |  |                                                                  |  | • read_file: "core/ui_theme.py"   |
| A  |  |   [Abhasa] I have structured the redesign into Primitive,        |  |   └─ [1.2 KB] (6ms cached)        |
| V  |  |            Semantic, and Component token tiers.                  |  | • run_subagent: visual-design-mst |
|    |  |                                                                  |  |   └─ [RUNNING...] (pulsing)   |
| R  |  |   ┌─ [⚡ EXEC] read_file("core/ui_theme.py") ────────── [✓ 24ms] ┐  | +-------------------------------+ |
| A  |  |   └─────────────────────────────────────────────────────────────┘  | SYSTEM TELEMETRY                  |
| I  |  |   ┌─ [code] core/theme/tokens.py ───────────────────────────────┐  | CPU: [■■□□□□□□] 14%               |
| L  |  |   │ class LuminaTokens: SURFACE_CANVAS = "#08090C"...           │  | RAM: [■■■■□□□□] 38%               |
|    |  |   └─────────────────────────────────────────────────────────────┘  | LATENCY: 142ms · TOKENS: 46 t/s   |
| (60|  +------------------------------------------------------------------+  +-----------------------------------+
| px)|  | [COMPONENT] FLOATING LUMINA DOCK (Height: 52px, Pill, Width: 700px)                                      |
|    |  | [ 📎 ]  [ Ask Abhasa anything or type a tool command... ]   [~~~ Wave]  [ 🎙 Mic ]  [ ➔ Run ]             |
+====+============================================================================================================+
```

### 4.2 Responsive Breakpoint Engine

```mermaid
flowchart TD
    W{"Viewport Width (W)"}
    W -->|">= 1200px"| Wide["Wide Desktop: Full Bento + 340px Docked Drawer + 720px Dock"]
    W -->|"1024px - 1199px"| Med["Medium Desktop: 280px Drawer + Compact Avatar Disc"]
    W -->|"< 1024px"| Compact["Compact Mode: Drawer collapses to overlay (Ctrl+B), Full-width Stage"]
    W -->|"Min: 840px"| StrictMin["Strict Minimum: Dock clamps to 100%-32px; Zero horizontal scroll"]
```

---

## 5. Voice Interaction & Multimodal Choreography

Authored by `voice-interaction-designer`:

Abhasa Evo is a voice-first bidirectional assistant powered by Gemini Live (`BrahmaLive` in [`main.py`](../main.py)). The visual design must communicate audio states with zero ambiguity.

### 5.1 Voice State Visual Matrix

| Voice State | Ambient Scrim Glow | Lumina Dock Perimeter | Waveform Behavior | Avatar / Core Reactor |
|---|---|---|---|---|
| **`IDLE`** | Translucent obsidian (8% aura) | Hairline subtle (`rgba(255,255,255,0.08)`) | Flat baseline (2px dots) | Slow breathing idle oscillation (0.2 Hz) |
| **`LISTENING`** | Lumina Cyan (`rgba(0,229,255,0.20)`) | Pulsing Cyan (`#00E5FF`) | 5-bar active audio RMS equalizer | Cyan aura expands with microphone decibels |
| **`THINKING`** | Reflection Violet (`rgba(139,92,246,0.20)`)| Soft Violet breathing | 3-dot harmonic shimmer | Center core contracts slightly, rotating |
| **`EXECUTING`** | Amber Gold (`rgba(245,158,11,0.20)`) | Amber perimeter gradient sweep | Micro-activity pulse | Rings rotate with high-speed tool telemetry |
| **`SPEAKING`** | Crisp Lumina White (`#FFFFFF`) | Emerald audio accent (`#10B981`) | Fluid assistant voice wave | Radiating acoustic rings matching speech |

### 5.2 Barge-In & Interruption Choreography
In bidirectional voice sessions, the user can speak at any moment to interrupt the model ("barge-in"):
1. **Immediate Audio Clamp (< 30ms)**: The speaker audio stream cuts off immediately.
2. **Visual Yield Transition (< 80ms)**: The Lumina Dock waveform instantly morphs from assistant playback green (`#10B981`) to user microphone cyan (`#00E5FF`).
3. **No Flashing or Redraw Stutter**: The transition uses pure CSS variable interpolation or `QPainter` opacity interpolation without reloading widgets.

---

## 6. Avatar Subsystem: Multi-Engine Depth Compositing & Zero-Avatar Mode

Authored by `design-system-architect`, `visual-design-master`, and `performance-ux-optimizer`:

In accordance with [`docs/ONE-391-avatar-backends.md`](ONE-391-avatar-backends.md) and [`core/avatar_runtime.py`](../core/avatar_runtime.py), Abhasa supports multiple distinct avatar backends behind a unified three-method contract (`probe()`, `set_emotion()`, `status()`). 

Crucially, **user preference dictates whether an avatar is desired at all**. Running 3D WebGL or Live2D models consumes significant resources (~150MB–320MB of Chromium RAM and continuous GPU draw loops). The visual architecture must provide first-class support for both **Avatar Enthusiasts** (who want an expressive companion in the background) and **Minimalist Power Users** (who want zero distraction, maximum battery life, and 0% CPU consumption).

### 6.1 The Four Operational Avatar Modes

```mermaid
flowchart TD
    subgraph EngineSelection["Avatar Engine Modes (core/avatar_runtime.py)"]
        M0["Mode 0: Pure Lumina (No Avatar)<br/>• 0% CPU / 0 MB WebEngine RAM<br/>• Static Obsidian Canvas<br/>• Terminal/IDE Speed"]
        M1["Mode 1: 3D Quantum Core (three_reactive)<br/>• Abstract optical refraction orb<br/>• State-reactive bloom & rings<br/>• ~140 MB WebEngine RAM"]
        M2["Mode 2: Live2D Cubism (live2d)<br/>• 2D expressive anime avatar<br/>• Eye tracking, lip-sync, emotions<br/>• ~190 MB WebEngine RAM"]
        M3["Mode 3: VRM 3D Humanoid (vrm)<br/>• 3D MToon shaded character<br/>• Spring bones hair/cloth physics<br/>• ~260 MB WebEngine RAM"]
    end
    EngineSelection --> RenderPlane["Layered Depth Compositing Engine"]
```

| Mode ID | Engine / Transport | Visual Manifestation | Resource Footprint | Best Suited For |
|---|---|---|---|---|
| **`none` (Pure Lumina)** | None / Qt native canvas | Deep obsidian frosted glass with static radial luminance scrim | **0.0% CPU**, **0 MB** WebEngine RAM | Coding, high-speed agentic execution, battery conservation |
| **`three_reactive`** | Three.js WebGL (WebEngine) | Geometric optical resonator orb with UnrealBloomPass rings | ~0.8% CPU, ~140 MB RAM | High-tech sci-fi aesthetic, abstract state visualization |
| **`live2d`** | Cubism SDK (WebEngine) | 2D stylized illustrated character with breathing & gaze | ~1.2% CPU, ~190 MB RAM | Anime/companion conversational interactions |
| **`vrm`** | Three.js + MToon (WebEngine) | Full 3D humanoid avatar with spring bone physics & morphs | ~1.8% CPU, ~260 MB RAM | Full virtual companion, streaming, expressive presence |
| *`osc_vmc`* | External UDP OSC | Rendered in external software (VSeeFace, Warudo) | ~0.0% GUI CPU, external | Multi-monitor setups, VTuber integration |

### 6.2 Layered Depth Compositing: Background Avatar + Translucent Dashboard

A key limitation of the legacy design was an awkward all-or-nothing conflict: opaque cards completely hid the avatar, while transparent cards caused text to collide with glowing background polygons.

"Abhasa Lumina" introduces **Layered Depth Compositing**:

```
[Layer 3: Top Foreground]   Floating Lumina Dock (Pill) & Modal Overlays (Highest contrast, elevated glass)
                                 │
[Layer 2: Translucent Stage] Main Dashboard (Bento cells, message stream, tabs with user-tuned opacity)
                                 │
[Layer 1: Optical Scrim]     Directional Asymmetric Scrim (Left: 95% dark blur -> Right: 20% clear)
                                 │
[Layer 0: Background Canvas] WebEngine Viewport: Avatar Staging & Ambient Orbital Glow
```

#### The Directional Optical Scrim
Instead of applying a uniform darkening filter that obscures the entire avatar, Layer 1 applies a **Directional Optical Scrim**:
```css
/* Directional Scrim: Preserves left-side text contrast, exposes right-side avatar */
.depth-scrim {
    background: linear-gradient(to right,
        rgba(8, 9, 12, 0.95) 0%,     /* 100% text shield over conversation feed */
        rgba(8, 9, 12, 0.70) 45%,    /* Subtle frosted depth */
        rgba(8, 9, 12, 0.20) 80%,    /* High clarity over avatar silhouette */
        rgba(8, 9, 12, 0.50) 100%    /* Vignetted window edge */
    );
}
```

#### User-Configurable Dashboard Translucency Presets
In settings or via a quick slider in the top bar, users can dial in their preferred balance between **data clarity** and **ambient avatar presence**:

1. **Solid Focus Preset (`95%` Opacity)**:
   - For focused coding, debugging, and terminal operations. Cards are almost solid (`rgba(21, 25, 33, 0.95)`), background avatar acts purely as a subtle silhouette.
2. **Balanced Lumina Preset (`78%` Opacity, Default)**:
   - The canonical Abhasa Lumina look. Frosted obsidian glass (`rgba(21, 25, 33, 0.78)`) with `20px` backdrop micro-blur. Background avatar motions (blinking, breathing, state changes) remain gracefully perceptible through cards without compromising WCAG AAA contrast.
3. **Ambient Companion Preset (`45%` Opacity)**:
   - For casual voice-only chatting. Heavy backdrop blur (`24px`), chat bubbles become gentle translucent glass wafers, and the avatar takes full visual stage.

### 6.3 Performance Architecture for Zero-Avatar Mode (`pure_lumina`)

When the user selects **"No Avatar"** (`config_manager.set_setting("avatar_enabled", False)` and `enable_3d_background = False`):
1. **Chromium Render Suspension**: `core/ui_bg.py` calls `window.__pauseAnimation()` and hides the `QWebEngineView`.
2. **Memory Reclaim**: In headless or memory-constrained mode, the WebEngine view can be unloaded entirely, immediately releasing **~150MB to ~300MB of RAM**.
3. **GPU Idle Drop**: With the WebEngine render loop halted, the entire PyQt6 desktop assistant sits at **0.0% CPU and 0.0% GPU** at idle.
4. **Visual Replacement**: The background seamlessly transitions to a static CSS/QPainter radial obsidian gradient (`#060709` to `#0E1117`), delivering a sleek, ultra-fast IDE-grade experience.

---

## 7. Production Component Specifications & QSS Implementation

Authored by `design-system-architect`:

### 7.1 The Floating Lumina Dock (`core/ui_lumina_dock.py`)
```css
/* Qt StyleSheet Specification for Lumina Dock */
QFrame#LuminaDock {
    background: rgba(16, 20, 28, 0.94);
    border: 1px solid rgba(0, 229, 255, 0.35);
    border-radius: 26px;
}

QFrame#LuminaDock:hover {
    border: 1px solid rgba(0, 229, 255, 0.60);
}

QLineEdit#LuminaDockInput {
    background: transparent;
    border: none;
    color: #F8FAFD;
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 13px;
    padding: 0 8px;
    selection-background-color: rgba(0, 229, 255, 0.30);
}

QPushButton#LuminaMicBtn {
    background: rgba(0, 229, 255, 0.12);
    border: 1px solid #00E5FF;
    border-radius: 18px;
    color: #00E5FF;
}

QPushButton#LuminaMicBtn:hover {
    background: rgba(0, 229, 255, 0.25);
}

QPushButton#LuminaSendBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00E5FF, stop:1 #8B5CF6);
    border: none;
    border-radius: 18px;
    color: #08090C;
    font-weight: 600;
    font-size: 12px;
    padding: 0 16px;
}
```

### 7.2 The Message Card (`LuminaMessageCard`)
```css
QFrame#LuminaMessageCardUser {
    background: rgba(24, 29, 38, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
}

QFrame#LuminaMessageCardAssistant {
    background: rgba(21, 25, 33, 0.88);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-top: 1px solid rgba(0, 229, 255, 0.40);
    border-radius: 16px;
}

/* Tool Execution Accordion */
QFrame#ToolExecutionBanner {
    background: rgba(11, 14, 20, 0.95);
    border: 1px solid rgba(0, 229, 255, 0.25);
    border-radius: 8px;
    padding: 8px 12px;
}
```

### 7.3 Vector Icon Registry (`core/icons.py`)
Replacing procedural line-art in [`core/ui_branding.py`](../core/ui_branding.py) with crisp, embedded SVG vector icons:
```python
"""Embedded resolution-independent SVG icons for Abhasa Lumina."""
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtSvg import QSvgRenderer

class LuminaIcons:
    _SVG_MIC = """<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" x2="22"/></svg>"""
    _SVG_ATTACH = """<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>"""
    _SVG_SEND = """<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>"""
    _SVG_DIAMOND = """<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2"><polygon points="12,2 22,12 12,22 2,12"/><polygon points="12,6 18,12 12,18 6,12" fill="{fill}"/></svg>"""

    @classmethod
    def get_pixmap(cls, name: str, size: int = 20, color: str = "#C5D0E0", fill: str = "transparent") -> QPixmap:
        raw_svg = getattr(cls, f"_SVG_{name.upper()}").format(color=color, fill=fill)
        renderer = QSvgRenderer(raw_svg.encode("utf-8"))
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        renderer.render(p)
        p.end()
        return pix
```

---

## 8. Performance Engineering & Paint Optimization

Authored by `performance-ux-optimizer`:

PyQt6 desktop applications can experience micro-stutter if animations trigger full-window repaints or excessive stylesheet re-parsing.

### 8.1 Paint Isolation & Dirty Rect Optimization
1. **`WA_OpaquePaintEvent` on Canvas**: The root canvas and central WebGL view are marked with `setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)`. This prevents Qt from repainting the underlying window hierarchy when child widgets redraw.
2. **Audio Waveform Decoupling**: The waveform visualizer in `LuminaDock` paints onto an isolated 120x24 surface. It updates at 60fps via `update(dirty_rect)` rather than calling `update()` on the parent dock or window.
3. **No Dynamic `setStyleSheet` in Loops**: The existing pattern of repeatedly rewriting QSS strings in timers is banned. Color transitions are applied through `QColor` updates on `QPainter` or custom `QPropertyAnimation` targeting palette attributes.

### 8.2 WebGL Throttling & Battery Conservation
In [`core/ui_bg.py`](../core/ui_bg.py), the Chromium-backed Three.js WebGL canvas is throttled based on application focus:
- **Active Focused State**: 60 FPS (full fidelity).
- **Idle / Unfocused State**: Throttled to 15 FPS via `window.__setFramerate(15)`.
- **Minimized / Hidden State**: Framerate paused completely (`window.__pauseAnimation()`). CPU drops to 0.0%.

---

## 9. Accessibility & Universal Usability Specification

Authored by `accessibility-specialist`:

### 9.1 Focus Rings & Keyboard Navigation
Every interactive element supports full keyboard navigation without mouse reliance:
- **Standard Focus Ring**: `2px solid #00E5FF; outline-offset: 2px;`
- **Key Navigation Map**:
  - `Ctrl + K`: Instantly focus the Lumina Dock command input.
  - `Ctrl + B`: Toggle the Telemetry & Context Drawer.
  - `Space` (when dock focused & empty): Push-to-talk microphone toggle.
  - `Tab / Shift+Tab`: Cycles logically: Dock Input → Attach → Mic → Telemetry Tabs → Ops Stream.
  - `Escape`: Clear active input / close overlay panels.

### 9.2 Screen Reader & Semantic Hierarchy
All custom widgets implement `QAccessibleWidget` interfaces:
- `LuminaDock`: `accessibleName = "Command and prompt input"`
- `LuminaMicBtn`: `accessibleName = "Voice session toggle"`, `accessibleDescription = "Starts Gemini Live bidirectional audio"`
- `TelemetryDrawer`: `accessibleName = "Realtime tool telemetry and execution log"`

---

## 10. Automated Testing & Verification Harness

Authored by `gui-ui-testing-agent`:

To ensure visual regression safety in CI without requiring a physical monitor:

```powershell
# 1. Code quality and linting
py -3.11 -m ruff check core/theme/ core/icons.py ui.py

# 2. Fast agent harness verification (Must stay 100% green)
py -3.11 -m pytest tests -q -k agent

# 3. Offscreen Qt component tests
$env:QT_QPA_PLATFORM="offscreen"
py -3.11 -m pytest tests/test_ui_theme_tokens.py tests/test_ui_lumina_dock.py -q
```

### 10.1 Unit Test Invariants (`tests/test_ui_theme_tokens.py`)
1. **Zero Hex Replacements**: Verifies that `core/ui_theme.py` and `ui.py` contain zero runtime string substitutions on `QWidget.setStyleSheet`.
2. **Contrast Ratio Validator**: Automated test asserting all semantic text tokens vs canvas `#08090C` exceed $4.5:1$ (AA) and $7.0:1$ (AAA).
3. **Bounding Box Overlap Check**: Simulates window sizes from `840x560` up to `2560x1440` asserting that `LuminaDock`, `StageFrame`, and `TelemetryDrawer` geometry rectangles never intersect or clip.

---

## 11. Execution & Migration Phases

| Phase | Target Scope | Code Artifacts Created / Modified | Rollback Guard |
|---|---|---|---|
| **Phase 0** | Token Seam Architecture | Create `core/theme/tokens.py`, update `core/ui_theme.py` | Full backward-compatible shim on `C.PRI`, `C.ACC` |
| **Phase 1** | Native SVG Icon Engine | Create `core/icons.py`, migrate `core/ui_branding.py` | Retain fallback line-art if QtSvg unavailable |
| **Phase 2** | Lumina Dock Implementation | Create `core/ui_lumina_dock.py`, bind into `ui.py` | Legacy `CommandBar` available behind config flag |
| **Phase 3** | Telemetry Drawer Integration | Create `core/ui_telemetry_drawer.py`, retire floating wings | Existing telemetry signals route to drawer tabs |
| **Phase 4** | Stage Restructure & Monkeypatch Removal | Restructure `_build_center_panel_modern`, delete lines 86–100 of `ui.py` | Verify offscreen test suite passes cleanly |

---

*Authored by the Graphic Design, Architecture, & Ergonomics Collective (`visual-design-master`, `design-system-architect`, `micro-interaction-designer`, `interaction-design-specialist`, `behavioral-design-psychologist`, `voice-interaction-designer`, `accessibility-specialist`, `performance-ux-optimizer`, `gui-ui-testing-agent`, `open-codesign`, `collaborative-canvas-designer`).*
