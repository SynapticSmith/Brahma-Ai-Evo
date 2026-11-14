# Theme templates

Drop-in colour palettes for the Brahma Evo shell. Read by
`core/ui_theme.py` (`C.load_theme_templates`, `C.apply_theme_value`) and
selectable in **Settings → App Theme → Theme Template**.

Nothing here needs a restart: picking a template stores its slug in
`app_theme` and re-tints the live UI.

## Ownership

This directory is shared by two trees, and the file names are the boundary.

| Files | Owner | Notes |
| --- | --- | --- |
| `brahma-quantum-dark.json`, `brahma-cyber-amber.json`, `brahma-paper-light.json`, `brahma-slate-frost.json` | upstream Brahma (`titechprabhasolutions/Brahma-Ai-Evo`) | Keep in step with `docs/BRAHMA-THEME-SCHEMA-AND-AGENT-CONTRACT.md`. |
| `canonical-citra.json`, `amber-crt.json`, `nordic-frost.json`, `paper-solar-light.json` | the Abhasa / Citra fork | Fork design assets. Upstream ships them unchanged and must not "fix" them into the Brahma layout. |

The four fork files currently mirror the four Brahma ones token for token (same
colours, same values, only the group names differ). That is expected, not a
loader bug — do not "deduplicate" them; the fork owns its copies and will
diverge.

If you are working in the fork, add new files under the fork's own naming.
If you are working upstream, do not rename or convert a fork file — the fork
is cut from this tree.

## Schema

`canvas` / `border` / `palette` is **canonical**, because it is what the
upstream contract documents and what the four `brahma-*.json` files use.

```json
{
  "schema_version": "1.0",
  "name": "Brahma Quantum Dark",
  "slug": "brahma-quantum-dark",
  "author": "Brahma Design Team",
  "description": "…",
  "variant": "dark",
  "tokens": {
    "canvas":  { "base": "", "surface_1": "", "surface_2": "", "glass": "",
                 "scrim_left": "", "scrim_right": "" },
    "border":  { "subtle": "", "hover": "", "active": "",
                 "radius_sm": 4, "radius_md": 10, "radius_lg": 16, "radius_pill": 26 },
    "palette": { "accent_primary": "", "accent_secondary": "", "accent_voice": "",
                 "signal_success": "", "signal_warning": "", "signal_danger": "",
                 "signal_info": "" },
    "text":    { "primary": "", "secondary": "", "muted": "", "inverse": "" },
    "avatar":  { "default_mode": "three_reactive", "dashboard_translucency": 0.78,
                 "ambient_glow": "" }
  }
}
```

`slug` is the only required field. Unknown groups and keys are preserved, not
rejected, so a template may carry tokens the loader does not consume yet. A
file that fails to parse is skipped; it never takes the picker down with it.

### The fork layout is an alias, not a second schema

The Abhasa templates spell the same three groups `bhitti` / `citra` / `ranga`.
`normalize_theme_tokens()` maps them onto the canonical names, so both layouts
load:

| Fork | Canonical | Fork key | Canonical key |
| --- | --- | --- | --- |
| `bhitti` | `canvas` | — | — |
| `citra` | `border` | `border_subtle` | `subtle` |
| | | `border_hover` | `hover` |
| | | `border_active` | `active` |
| `ranga` | `palette` | `accent_reason` | `accent_secondary` |
| — | — | everything else | same name |

`text` and `avatar` are already identical in both.

## How a template reaches the palette

`tokens.canvas.base` → `C.BG`, `surface_1` → `C.PANEL`, `surface_2` →
`C.PANEL2`; `border.subtle/hover/active` → `C.BORDER`, `C.BORDER_A`,
`C.BORDER_B`; `palette.accent_primary` → `C.ACC` / `C.MUTED_C` / `C.ICE`;
`accent_secondary` → `C.ACC2`; `signal_success` → `C.GREEN`; `signal_danger`
→ `C.RED`; `text.primary` → `C.PRI` / `C.TEXT`, `secondary` → `C.PRI_DIM` /
`C.TEXT_MED`, `muted` → `C.TEXT_DIM`; `avatar.ambient_glow` → `C.ICE_GHO`.
`C.ICE_DIM`, `C.GREEN_D` and `C.BAR_BG` are derived.

A plain hex or one of the legacy words (`blue`, `green`, `red`, `ice`,
`white`) in `app_theme` still works and produces exactly the palette it always
did — that path is `C.load_theme`, unchanged.

## Adding a template

1. Copy a file in the **same** layout you are extending (upstream → `brahma-*.json`,
   fork → its own name).
2. Change `slug`, `name`, and the tokens you care about.
3. It appears in the Settings picker on the next page build; no registration
   list to edit.
