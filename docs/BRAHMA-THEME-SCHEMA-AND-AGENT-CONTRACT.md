# Brahma Evo — Theme Template Schema & Agent Redesign Contract

| Field | Value |
|---|---|
| **Document** | `docs/BRAHMA-THEME-SCHEMA-AND-AGENT-CONTRACT.md` |
| **Status** | Authoritative Design System Specification & Agent Execution Contract |
| **Date** | 2026-11-10 |
| **Target Platform** | Windows-first PyQt6 Desktop Assistant (`Brahma AI / Brahma Evo`) |
| **Upstream Target** | `titechprabhasolutions/Brahma-Ai-Evo` |
| **Governing Authority** | [`AGENTS.md`](../AGENTS.md), [`docs/BRAHMA-VISUAL-REDESIGN-PROPOSAL.md`](BRAHMA-VISUAL-REDESIGN-PROPOSAL.md), [`docs/CURRENT-VISUAL-INVENTORY.md`](CURRENT-VISUAL-INVENTORY.md) |
| **Template Storage Path** | Bundled: `templates/themes/*.json` · User: `config/themes/*.json` (or `%LOCALAPPDATA%\Brahma\themes\`) |

---

## 0. Purpose & Golden Invariants

### 0.1 Why This Contract Exists
Users frequently ask AI coding assistants to modify or redesign an application's appearance (e.g., *"Make the UI look like a warm 1980s Amber CRT terminal"*, *"Give me a high-contrast solarized theme for outdoor work"*, or *"Redesign the palette to Nordic frost"*). 

In legacy codebases, an agent attempting a visual redesign directly edits imperative Python files (`ui.py`, `core/ui_*.py`), clobbers signal connections, breaks widget layouts, or introduces runtime syntax errors. 

This document establishes the **Declarative Theme Boundary for Brahma Evo**:
> **The Golden Invariant for Coding Agents:**  
> When tasked with a visual redesign, color palette modification, theme generation, or aesthetic adjustment, **THE AGENT MUST NEVER MODIFY PYTHON CODE (`.py` files)**.  
>  
> The agent MUST author or update a declarative JSON theme template in `config/themes/<theme-slug>.json` conforming to this contract, and invoke `ThemeManager.apply_theme("<theme-slug>")`.

### 0.2 Principles of the Brahma Theme Engine
1. **Separation of Structure and Styling**: The structural chassis (PyQt6 `QWidget`, layout geometry, signal wiring) is immutable; the visual skin (colors, opacities, borders, radii) is 100% declarative.
2. **Zero-Restart Live Reload**: Applying a theme re-evaluates the QSS token variables and triggers `QWidget.update()` across the active stage in `< 16ms` without restarting the application.
3. **100% Surface Coverage**: The token schema maps directly to all 89 visual surfaces audited in [`docs/CURRENT-VISUAL-INVENTORY.md`](CURRENT-VISUAL-INVENTORY.md). No surface is left unstyled or invisible.
4. **Guaranteed Contrast & Accessibility**: All generated themes must pass automated WCAG 2.2 / APCA contrast gates before activation.

---

## 1. Informational Reference Table (The 89-Surface Token Mapping)

Every theme template must populate the following semantic token categories. The table below links each token to its functional role and impacted surfaces:

| Category | Token Key | Format | Default Value | Functional Role & Impacted Codebase Surfaces |
|---|---|---|---|---|
| **Canvas & Surfaces** | `canvas.base` | Hex `#RRGGBB` | `#060709` | Root OS window canvas (`QMainWindow`, `BackgroundWidget`), outer edge margins. |
| | `canvas.surface_1` | Hex `#RRGGBB` | `#08090C` | Bento stage base, central conversation stream backdrop, settings container. |
| | `canvas.surface_2` | Hex `#RRGGBB` | `#0E1117` | Elevated content panels, chat cards, telemetry cards (`SmallPanelCard`, `SetupOverlay`). |
| | `canvas.glass` | RGBA `rgba(r,g,b,a)` | `rgba(14, 17, 23, 0.82)` | Semi-transparent floating panels, popups, and translucent cards over the background avatar. |
| | `canvas.scrim_left` | RGBA `rgba(r,g,b,a)` | `rgba(8, 9, 12, 0.95)` | Left-hand text shield in the asymmetric optical scrim (ensures WCAG AAA contrast). |
| | `canvas.scrim_right`| RGBA `rgba(r,g,b,a)` | `rgba(8, 9, 12, 0.20)` | Right-hand avatar exposure window in the asymmetric optical scrim. |
| **Borders & Radii** | `border.subtle` | RGBA `rgba(r,g,b,a)` | `rgba(255, 255, 255, 0.08)` | Default hairline dividers, card borders, table separators. |
| | `border.hover` | RGBA `rgba(r,g,b,a)` | `rgba(255, 255, 255, 0.18)` | Hovered cards, unfocused active text inputs, button perimeters. |
| | `border.active` | RGBA `rgba(r,g,b,a)` | `rgba(0, 229, 255, 0.35)` | Focused inputs (`BrahmaDockInput`), active tab indicators, selected cards. |
| | `border.radius_sm` | Integer (px) | `4` | Badges, tool chips, mini telemetry indicators. |
| | `border.radius_md` | Integer (px) | `10` | Bento cards, dialogs, code blocks (`ToolExecutionBanner`). |
| | `border.radius_lg` | Integer (px) | `16` | Message cards (`BrahmaMessageCardUser`, `BrahmaMessageCardAssistant`). |
| | `border.radius_pill`| Integer (px) | `26` | Floating command island (`BrahmaDock`), search capsules. |
| **Color Palette** | `palette.accent_primary` | Hex `#RRGGBB` | `#00E5FF` | Action CTA, mic pulse, primary focus glow, radar sweeping beam (`RadarPopupOverlay`). |
| | `palette.accent_secondary`| Hex `#RRGGBB` | `#8B5CF6` | Subagent execution, reasoning tokens, planning states, code diff additions. |
| | `palette.accent_voice` | Hex `#RRGGBB` | `#10B981` | Realtime assistant speech waveform, bidirectional audio active state. |
| | `palette.signal_success` | Hex `#RRGGBB` | `#10B981` | Tool exit code 0, security allow, checkmarks, operational health. |
| | `palette.signal_warning` | Hex `#RRGGBB` | `#F59E0B` | Rate limit warnings, approval prompts (`confirm.py`), pending actions. |
| | `palette.signal_danger` | Hex `#RRGGBB` | `#EF4444` | Tool execution errors, network failure, policy denial, emergency abort (`Ctrl+Shift+X`). |
| | `palette.signal_info` | Hex `#RRGGBB` | `#3B82F6` | Informational badges, file path chips, documentation links. |
| **Text Hierarchy** | `text.primary` | Hex `#RRGGBB` | `#F8FAFD` | Headings, main assistant responses, active command input. **Must achieve >= 7.0:1 contrast**. |
| | `text.secondary` | Hex `#RRGGBB` | `#C5D0E0` | Subtitles, tool arguments, telemetry values, card titles. **Must achieve >= 4.5:1 contrast**. |
| | `text.muted` | Hex `#RRGGBB` | `#8C9BB4` | Timestamps, placeholders, inactive tabs, subtle hints. **Must achieve >= 3.0:1 contrast**. |
| | `text.inverse` | Hex `#RRGGBB` | `#08090C` | Text rendered on top of solid accent buttons (e.g. `BrahmaSendBtn`). |
| **Avatar & Engine** | `avatar.default_mode` | String Enum | `"three_reactive"` | `"none"` (0% CPU), `"three_reactive"`, `"live2d"`, or `"vrm"`. |
| | `avatar.dashboard_translucency` | Float (`0.0` - `1.0`) | `0.78` | Glass card opacity (`0.95` Solid Focus, `0.78` Balanced, `0.45` Ambient Companion). |
| | `avatar.ambient_glow` | RGBA `rgba(r,g,b,a)` | `rgba(0, 229, 255, 0.15)` | Subtle backdrop aura reflecting active state. |

---

## 2. Formal JSON Schema Specification

Theme files are validated against the following schema before being loaded by `core/theme/engine.py`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "BrahmaEvoTheme",
  "type": "object",
  "required": [
    "schema_version",
    "name",
    "slug",
    "author",
    "tokens"
  ],
  "properties": {
    "schema_version": { "type": "string", "const": "1.0" },
    "name": { "type": "string", "minLength": 3, "maxLength": 48 },
    "slug": { "type": "string", "pattern": "^[a-z0-9-]+$" },
    "author": { "type": "string" },
    "description": { "type": "string" },
    "variant": { "type": "string", "enum": ["dark", "light", "monochrome", "high_contrast"] },
    "tokens": {
      "type": "object",
      "required": ["canvas", "border", "palette", "text", "avatar"],
      "properties": {
        "canvas": {
          "type": "object",
          "required": ["base", "surface_1", "surface_2", "glass", "scrim_left", "scrim_right"],
          "properties": {
            "base": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "surface_1": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "surface_2": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "glass": { "type": "string", "pattern": "^rgba?\\(.*\\)$" },
            "scrim_left": { "type": "string", "pattern": "^rgba?\\(.*\\)$" },
            "scrim_right": { "type": "string", "pattern": "^rgba?\\(.*\\)$" }
          }
        },
        "border": {
          "type": "object",
          "required": ["subtle", "hover", "active", "radius_sm", "radius_md", "radius_lg", "radius_pill"],
          "properties": {
            "subtle": { "type": "string", "pattern": "^rgba?\\(.*\\)$" },
            "hover": { "type": "string", "pattern": "^rgba?\\(.*\\)$" },
            "active": { "type": "string", "pattern": "^rgba?\\(.*\\)$" },
            "radius_sm": { "type": "integer", "minimum": 0, "maximum": 16 },
            "radius_md": { "type": "integer", "minimum": 0, "maximum": 24 },
            "radius_lg": { "type": "integer", "minimum": 0, "maximum": 32 },
            "radius_pill": { "type": "integer", "minimum": 16, "maximum": 40 }
          }
        },
        "palette": {
          "type": "object",
          "required": ["accent_primary", "accent_secondary", "accent_voice", "signal_success", "signal_warning", "signal_danger", "signal_info"],
          "properties": {
            "accent_primary": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "accent_secondary": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "accent_voice": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "signal_success": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "signal_warning": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "signal_danger": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "signal_info": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" }
          }
        },
        "text": {
          "type": "object",
          "required": ["primary", "secondary", "muted", "inverse"],
          "properties": {
            "primary": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "secondary": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "muted": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" },
            "inverse": { "type": "string", "pattern": "^#[0-9a-fA-F]{6}$" }
          }
        },
        "avatar": {
          "type": "object",
          "required": ["default_mode", "dashboard_translucency", "ambient_glow"],
          "properties": {
            "default_mode": { "type": "string", "enum": ["none", "three_reactive", "live2d", "vrm"] },
            "dashboard_translucency": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
            "ambient_glow": { "type": "string", "pattern": "^rgba?\\(.*\\)$" }
          }
        }
      }
    }
  }
}
```

---

## 3. Contrast Calculation & Invariant Verification Script

Coding agents must verify that their proposed theme satisfies WCAG 2.2 AA / AAA contrast standards before saving. The relative luminance calculation is defined as:

$$L = 0.2126 \times R_{sRGB} + 0.7152 \times G_{sRGB} + 0.0722 \times B_{sRGB}$$

$$\text{Contrast Ratio} = \frac{L_1 + 0.05}{L_2 + 0.05} \quad (\text{where } L_1 > L_2)$$

### Python Validator Snippet for Agents:
```python
def check_theme_contrast(theme_dict: dict) -> tuple[bool, str]:
    def rel_lum(hex_c: str) -> float:
        r, g, b = [int(hex_c[i:i+2], 16) / 255.0 for i in (1, 3, 5)]
        def ch(c): return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

    bg_lum = rel_lum(theme_dict["tokens"]["canvas"]["surface_1"])
    tx_lum = rel_lum(theme_dict["tokens"]["text"]["primary"])
    l1, l2 = max(bg_lum, tx_lum), min(bg_lum, tx_lum)
    ratio = (l1 + 0.05) / (l2 + 0.05)
    
    if ratio < 4.5:
        return False, f"Contrast failure: Primary text vs surface_1 ratio is {ratio:.2f}:1 (Must be >= 4.5:1)"
    return True, f"Contrast PASS: {ratio:.2f}:1"
```

---

## 4. Agent Execution SOP (Step-by-Step Redesign Runbook)

When an agent receives an instruction like:
> *"Hey Brahma, redesign your visual interface to feel like a retro amber CRT terminal"*

The agent must execute the following 5-step Standard Operating Procedure:

1. **Query the Reference Contract**: Read `docs/BRAHMA-THEME-SCHEMA-AND-AGENT-CONTRACT.md` to ensure all 27 required token keys are populated.
2. **Synthesize Palette & Test Contrast**: Ensure `text.primary` vs `canvas.surface_1` satisfies contrast requirements using the contrast validator.
3. **Write the JSON Template**: Save the file to `config/themes/<theme-slug>.json`. **Never alter any `.py` file.**
4. **Hot-Apply the Theme**: Execute `core.theme.engine.ThemeManager.apply_theme('<theme-slug>')`.
5. **Report to User**: Inform the user of the new template name and contrast rating.
