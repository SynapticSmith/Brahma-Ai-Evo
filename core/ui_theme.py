"""Colour palette ``C``, the theme-template loader and the QSS re-tint table.

Leaf module on purpose: stdlib only, no PyQt and no project imports. ``ui.py``
and the extracted ``ui_*`` modules read ``C.*`` at import time - some as default
argument values, which Python evaluates at def time - so anything heavier here
would create an import cycle.

Three front doors:

* :meth:`C.load_theme` - the original named/hex path. Output is unchanged, so a
  hand-edited ``app_theme`` of ``"#ffffff"`` or ``"Gold"`` still produces the
  pixel-identical palette it always did.
* :meth:`C.load_theme_tokens` - applies a parsed ``templates/themes/*.json``
  token block.
* :meth:`C.apply_theme_value` - accepts either a slug or a hex, so the shipped
  JSON templates are finally reachable from Settings.

Schema note: ``templates/themes`` ships two token layouts for the same data.
``canvas`` / ``border`` / ``palette`` is the Brahma contract
(``docs/BRAHMA-THEME-SCHEMA-AND-AGENT-CONTRACT.md``) and is canonical;
``bhitti`` / ``citra`` / ``ranga`` is the Abhasa/Citra fork's naming for those
same groups. :func:`normalize_theme_tokens` maps the fork layout onto the
canonical one, so both load and neither file has to be rewritten.
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

#: ``app_theme`` fallback. One constant instead of the three literals
#: ("#ffffff", "Gold", "#00e5ff") the call sites used to disagree on.
DEFAULT_APP_THEME = "#ffffff"

#: Word themes accepted before JSON templates existed. Still what a hand-edited
#: app_settings.json may contain, so they must keep working.
NAMED_THEMES = {
    "blue": "#007aff",
    "green": "#37ff5f",
    "red": "#ff3b30",
    "ice": "#00e5ff",
    "white": "#ffffff",
}

#: Fork group name -> canonical group name.
GROUP_ALIASES = {"bhitti": "canvas", "citra": "border", "ranga": "palette"}

#: Fork key -> canonical key, within the group it belongs to.
KEY_ALIASES = {
    "border": {
        "border_subtle": "subtle",
        "border_hover": "hover",
        "border_active": "active",
    },
    "palette": {
        "accent_reason": "accent_secondary",
    },
}

#: canonical token -> palette attributes it fills.
CANVAS_TOKENS = {"base": ("BG",), "surface_1": ("PANEL",), "surface_2": ("PANEL2",)}
BORDER_TOKENS = {"subtle": ("BORDER",), "hover": ("BORDER_A",), "active": ("BORDER_B",)}
PALETTE_TOKENS = {
    "accent_primary": ("ACC", "MUTED_C", "ICE"),
    "accent_secondary": ("ACC2",),
    "signal_success": ("GREEN",),
    "signal_danger": ("RED",),
}
TEXT_TOKENS = {
    "primary": ("PRI", "TEXT", "WHITE"),
    "secondary": ("PRI_DIM", "TEXT_MED"),
    "muted": ("TEXT_DIM",),
}
AVATAR_TOKENS = {"ambient_glow": ("ICE_GHO",)}

#: Hardcoded literals in older widget stylesheets that have to follow the
#: accent colour. Moved here from the ``QWidget.setStyleSheet`` monkeypatch in
#: ``ui.py``, which had grown a duplicated entry and could not be tested.
QSS_LITERALS = (
    "#00e5ff",
    "#ffd700",
    "#d4af37",
    "#00b4d8",
)
QSS_LITERAL_SECONDARY = "#80ffff"
#: Pre-formatted ``r, g, b`` strings sitting inside ``rgba(...)`` values.
QSS_RGB_LITERALS = (
    "255, 179, 0",
    "255,179,0",
    "255, 190, 26",
    "244, 180, 0",
)


def parse_hex(value) -> tuple[int, int, int] | None:
    """``"#rrggbb"`` -> ``(r, g, b)``, else ``None``. Never raises."""
    text = str(value or "").strip()
    if len(text) != 7 or not text.startswith("#"):
        return None
    try:
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    except ValueError:
        return None


def contrasting_text(theme_hex: str) -> str:
    """Ink colour that stays readable on ``theme_hex``. Never raises."""
    rgb = parse_hex(theme_hex)
    if rgb is None:
        return "#ffffff"
    luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return "#0b0c10" if luma > 150 else "#ffffff"


def _shift_lightness(theme_hex: str, delta: float) -> str:
    rgb = parse_hex(theme_hex)
    if rgb is None:
        return theme_hex
    h, l, s = colorsys.rgb_to_hls(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
    l = max(0.0, min(1.0, l + delta))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def normalize_theme_tokens(tokens) -> dict:
    """Return canonical token groups from either shipped layout.

    Unknown groups are kept under their original name and unknown keys survive,
    so a template may carry data this loader does not consume yet without being
    rejected. Raises ``TypeError`` when ``tokens`` is not a mapping.
    """
    if not isinstance(tokens, dict):
        raise TypeError("theme tokens must be an object")

    out: dict = {}
    for raw_group, value in tokens.items():
        group = GROUP_ALIASES.get(raw_group, raw_group)
        if not isinstance(value, dict):
            out[group] = value
            continue
        aliases = KEY_ALIASES.get(group, {})
        mapped = {}
        for raw_key, item in value.items():
            mapped[aliases.get(raw_key, raw_key)] = item
        # A template that carries both layouts for one group must not lose data:
        # the first spelling of a key wins, the alias only fills a gap.
        merged = dict(out.get(group) or {}) if isinstance(out.get(group), dict) else {}
        for key, item in mapped.items():
            merged.setdefault(key, item)
        out[group] = merged
    return out


def theme_from_dict(data) -> dict:
    """Validate one template document. Raises ``ValueError`` when unusable."""
    if not isinstance(data, dict):
        raise ValueError("theme document must be an object")
    slug = str(data.get("slug") or "").strip()
    if not slug:
        raise ValueError("theme document needs a slug")
    tokens = normalize_theme_tokens(data.get("tokens") or {})
    if not tokens:
        raise ValueError(f"theme {slug!r} has no tokens")
    return {
        "schema_version": str(data.get("schema_version") or "1.0"),
        "name": str(data.get("name") or slug),
        "slug": slug,
        "author": str(data.get("author") or ""),
        "description": str(data.get("description") or ""),
        "variant": str(data.get("variant") or "dark"),
        "tokens": tokens,
    }


def load_theme_file(path) -> dict:
    """Parse one ``*.json`` template. Raises ``OSError``/``ValueError``."""
    text = Path(path).read_text(encoding="utf-8")
    theme = theme_from_dict(json.loads(text))
    theme["path"] = str(path)
    return theme


def template_dir() -> Path:
    """The bundled ``templates/themes`` directory (may not exist in a build)."""
    return Path(__file__).resolve().parent.parent / "templates" / "themes"


def load_theme_templates(directory=None) -> list[dict]:
    """Every parseable template in ``directory``, sorted by slug.

    An unreadable or malformed file is skipped rather than raising: one bad
    template must not take the whole theme picker down with it.
    """
    base = Path(directory) if directory else template_dir()
    if not base.is_dir():
        return []
    themes = []
    for path in sorted(base.glob("*.json")):
        try:
            themes.append(load_theme_file(path))
        except Exception:
            continue
    themes.sort(key=lambda theme: theme["slug"])
    return themes


class C:
    #: Mirror of the module constant: most call sites (and ``bind``) hold ``C``
    #: rather than the module.
    DEFAULT_APP_THEME = DEFAULT_APP_THEME

    BG        = "#020305"
    PANEL     = "#07080b"
    PANEL2    = "#0d0f14"
    BORDER    = "rgba(0, 229, 255, 0.16)"
    BORDER_B  = "rgba(0, 229, 255, 0.35)"
    BORDER_A  = "rgba(255, 255, 255, 0.20)"
    PRI       = "#ffffff"
    PRI_DIM   = "#e2e8f0"
    PRI_GHO   = "rgba(255, 255, 255, 0.12)"
    ACC       = "#00e5ff"
    ACC2      = "#80ffff"
    GREEN     = "#37ff5f"
    GREEN_D   = "#1dcc43"
    RED       = "#ff3b30"
    MUTED_C   = "#00e5ff"
    TEXT      = "#f4f6f8"
    TEXT_DIM  = "#8e949d"
    TEXT_MED  = "#c5cad2"
    WHITE     = "#ffffff"
    DARK      = "#000000"
    BAR_BG    = "#222222"
    ICE       = "#00e5ff"
    ICE_DIM   = "#0099cc"
    ICE_GHO   = "rgba(0, 229, 255, 0.15)"

    #: Current theme metadata, for Settings and for anything that wants to know
    #: whether it is looking at a dark canvas.
    current_slug = ""
    current_name = "Default"
    current_variant = "dark"
    current_value = DEFAULT_APP_THEME

    _listeners: list = []

    @classmethod
    def is_dark(cls) -> bool:
        return cls.current_variant != "light"

    @classmethod
    def accent_rgb(cls) -> tuple[int, int, int]:
        return parse_hex(cls.ACC) or (0, 229, 255)

    @classmethod
    def register_listener(cls, callback):
        if callback not in cls._listeners:
            cls._listeners.append(callback)

    @classmethod
    def unregister_listener(cls, callback):
        if callback in cls._listeners:
            cls._listeners.remove(callback)

    @classmethod
    def _notify(cls):
        for cb in list(cls._listeners):
            try:
                cb()
            except Exception:
                pass

    # -- the original hex/named path -------------------------------------
    @classmethod
    def load_theme(cls, theme_hex: str):
        """Apply a single accent colour. Output must stay byte-identical."""
        theme_hex = str(theme_hex or "")
        if not theme_hex.startswith("#") or len(theme_hex) != 7:
            theme_hex = NAMED_THEMES.get(str(theme_hex).strip().lower(), "#ffffff")

        rgb = parse_hex(theme_hex)
        if rgb is None:
            rgb = (255, 255, 255)
            theme_hex = "#ffffff"
        r, g, b = rgb

        h, l, s = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)

        # Dim (darker by reducing l by 15%)
        l_dim = max(0.0, l - 0.15)
        r_dim, g_dim, b_dim = colorsys.hls_to_rgb(h, l_dim, s)
        hex_dim = f"#{int(r_dim*255):02x}{int(g_dim*255):02x}{int(b_dim*255):02x}"

        # Acc2 (lighter by increasing l by 15%)
        l_light = min(1.0, l + 0.15)
        r_light, g_light, b_light = colorsys.hls_to_rgb(h, l_light, s)
        hex_light = f"#{int(r_light*255):02x}{int(g_light*255):02x}{int(b_light*255):02x}"

        is_white = theme_hex.lower() in ("#ffffff", "#f4f6f8")

        cls.PRI       = theme_hex
        cls.PRI_DIM   = hex_dim
        cls.PRI_GHO   = f"rgba({r}, {g}, {b}, 0.15)"
        cls.ACC       = "#00e5ff" if is_white else theme_hex
        cls.ACC2      = "#80ffff" if is_white else hex_light
        cls.MUTED_C   = "#00e5ff" if is_white else theme_hex
        cls.BORDER    = f"rgba({r}, {g}, {b}, 0.15)"
        cls.BORDER_B  = "rgba(0, 229, 255, 0.35)" if is_white else f"rgba({r}, {g}, {b}, 0.25)"
        cls.BORDER_A  = f"rgba({r}, {g}, {b}, 0.18)"

        cls.current_slug = ""
        cls.current_name = "Custom"
        cls.current_variant = "light" if is_white else "dark"
        cls.current_value = theme_hex
        cls._notify()

    # -- the template path ------------------------------------------------
    @classmethod
    def load_theme_tokens(cls, theme: dict):
        """Apply a document produced by :func:`theme_from_dict`."""
        tokens = normalize_theme_tokens(theme.get("tokens") or {})

        # A template is a complete document, not a patch: start from the
        # built-in palette so a group it omits cannot inherit the previous
        # theme (which is how a red accent used to leave red borders behind).
        for attr, value in _DEFAULT_PALETTE.items():
            setattr(cls, attr, value)

        def group(name):
            value = tokens.get(name)
            return value if isinstance(value, dict) else {}

        def fill(mapping, values):
            for token, attrs in mapping.items():
                value = values.get(token)
                if isinstance(value, str) and value.strip():
                    for attr in attrs:
                        setattr(cls, attr, value.strip())

        fill(CANVAS_TOKENS, group("canvas"))
        fill(BORDER_TOKENS, group("border"))
        fill(PALETTE_TOKENS, group("palette"))
        fill(TEXT_TOKENS, group("text"))
        fill(AVATAR_TOKENS, group("avatar"))

        # Derived entries, so a template that only names the primary accent
        # still gets a coherent secondary/edge palette. A group this template
        # omits falls back to the built-in default rather than inheriting the
        # previously applied theme.
        cls.ICE = cls.ACC
        cls.ICE_DIM = _shift_lightness(cls.ACC, -0.15)
        if not group("palette").get("signal_success"):
            cls.GREEN = "#37ff5f"
        cls.GREEN_D = _shift_lightness(cls.GREEN, -0.10)
        cls.BAR_BG = cls.PANEL2

        cls.current_slug = str(theme.get("slug") or "")
        cls.current_name = str(theme.get("name") or cls.current_slug or "Custom")
        cls.current_variant = str(theme.get("variant") or "dark")
        cls.current_value = cls.current_slug
        cls._notify()

    @classmethod
    def apply_theme_value(cls, value) -> bool:
        """Apply a template slug or a hex/named colour, whichever ``value`` is."""
        text = str(value or "").strip()
        if not text:
            cls.load_theme(DEFAULT_APP_THEME)
            return True
        wanted = text.lower()
        for theme in load_theme_templates():
            if theme["slug"].lower() == wanted:
                cls.load_theme_tokens(theme)
                return True
        cls.load_theme(text)
        return True

    @classmethod
    def find_template(cls, value) -> dict | None:
        wanted = str(value or "").strip().lower()
        if not wanted:
            return None
        for theme in load_theme_templates():
            if theme["slug"].lower() == wanted:
                return theme
        return None

    @classmethod
    def load_theme_templates(cls, directory=None) -> list:
        """Mirror of the module function, for call sites that only hold ``C``."""
        return load_theme_templates(directory)

    # -- stylesheet re-tinting --------------------------------------------
    @classmethod
    def install_stylesheet_patch(cls, widget_class) -> None:
        """Route ``widget_class.setStyleSheet`` through :meth:`retint`.

        The pre-tint string is kept on the widget so :meth:`reapply_all` can
        re-run it after a theme change. Qt stays out of this module: the caller
        passes the class. Install is idempotent, so a double import is harmless.
        """
        current = widget_class.setStyleSheet
        if getattr(current, "_brahma_patched", False):
            return
        original = current

        def _patched(self, style):
            if isinstance(style, str) and style:
                self._brahma_raw_qss = style
                style = cls.retint(style)
            original(self, style)

        _patched.__name__ = "setStyleSheet"
        _patched._brahma_patched = True
        widget_class.setStyleSheet = _patched

    @classmethod
    def reapply_all(cls) -> int:
        """Re-tint every live widget that stored a pre-tint stylesheet."""
        from PyQt6.QtWidgets import QApplication  # local: keep this module Qt-free

        app = QApplication.instance()
        if app is None:
            return 0
        count = 0
        for widget in app.allWidgets():
            raw = getattr(widget, "_brahma_raw_qss", None)
            if not isinstance(raw, str) or not raw:
                continue
            try:
                widget.setStyleSheet(raw)
                count += 1
            except Exception:
                pass
        return count

    @classmethod
    def retint(cls, style: str) -> str:
        """Rewrite the legacy hardcoded accent literals to the live palette.

        Idempotent for every palette whose ``ACC`` is itself one of the source
        literals, which covers the built-in default. ``rgba`` triplets are
        derived from ``ACC`` rather than pinned to cyan, so a non-cyan theme no
        longer inherits cyan borders.
        """
        r, g, b = cls.accent_rgb()
        rgb_spaced = f"{r}, {g}, {b}"
        rgb_tight = f"{r},{g},{b}"
        for literal in QSS_LITERALS:
            style = style.replace(literal, cls.ACC)
        style = style.replace(QSS_LITERAL_SECONDARY, cls.ACC2)
        for literal in QSS_RGB_LITERALS:
            spacer = "" if "," in literal and " " not in literal else " "
            style = style.replace(literal, rgb_tight if not spacer else rgb_spaced)
        return style


#: Pristine palette, captured once after the class body runs. Applying a
#: template starts from here so it behaves as a complete document instead of a
#: patch over whatever theme was active before.
_DEFAULT_PALETTE = {name: value for name, value in vars(C).items() if name.isupper()}
