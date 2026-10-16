"""Color-palette class C, moved verbatim out of ui.py (P4-19).
Leaf module: no project imports. ui.py and extracted ui_* modules import C
directly, since some UI classes use C.* in default-argument values (def-time)."""
from __future__ import annotations


class C:
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
    _listeners: list = []

    @classmethod
    def register_listener(cls, callback):
        if callback not in cls._listeners:
            cls._listeners.append(callback)

    @classmethod
    def unregister_listener(cls, callback):
        if callback in cls._listeners:
            cls._listeners.remove(callback)

    @classmethod
    def load_theme(cls, theme_hex: str):
        if not theme_hex.startswith("#") or len(theme_hex) != 7:
            # Fallback to predefined colors if it's not a hex or just a word
            if theme_hex.lower() == "blue": theme_hex = "#007aff"
            elif theme_hex.lower() == "green": theme_hex = "#37ff5f"
            elif theme_hex.lower() == "red": theme_hex = "#ff3b30"
            elif theme_hex.lower() == "ice": theme_hex = "#00e5ff"
            elif theme_hex.lower() == "white": theme_hex = "#ffffff"
            else: theme_hex = "#ffffff"
            
        import colorsys
        try:
            r = int(theme_hex[1:3], 16)
            g = int(theme_hex[3:5], 16)
            b = int(theme_hex[5:7], 16)
        except Exception:
            r, g, b = 255, 255, 255
            theme_hex = "#ffffff"
            
        h, l, s = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)
        
        # Dim (darker by reducing l by 15%)
        l_dim = max(0.0, l - 0.15)
        r_dim, g_dim, b_dim = colorsys.hls_to_rgb(h, l_dim, s)
        hex_dim = f"#{int(r_dim*255):02x}{int(g_dim*255):02x}{int(b_dim*255):02x}"
        
        # Acc2 (lighter by increasing l by 15%)
        l_light = min(1.0, l + 0.15)
        r_light, g_light, b_light = colorsys.hls_to_rgb(h, l_light, s)
        hex_light = f"#{int(r_light*255):02x}{int(g_light*255):02x}{int(b_light*255):02x}"

        cls.PRI       = theme_hex
        cls.PRI_DIM   = hex_dim
        cls.PRI_GHO   = f"rgba({r}, {g}, {b}, 0.15)"
        cls.ACC       = "#00e5ff" if theme_hex.lower() in ("#ffffff", "#f4f6f8") else theme_hex
        cls.ACC2      = "#80ffff" if theme_hex.lower() in ("#ffffff", "#f4f6f8") else hex_light
        cls.MUTED_C   = "#00e5ff" if theme_hex.lower() in ("#ffffff", "#f4f6f8") else theme_hex
        cls.BORDER    = f"rgba({r}, {g}, {b}, 0.15)"
        cls.BORDER_B  = "rgba(0, 229, 255, 0.35)" if theme_hex.lower() in ("#ffffff", "#f4f6f8") else f"rgba({r}, {g}, {b}, 0.25)"
        cls.BORDER_A  = f"rgba({r}, {g}, {b}, 0.18)"

        for cb in list(cls._listeners):
            try:
                cb()
            except Exception:
                pass
