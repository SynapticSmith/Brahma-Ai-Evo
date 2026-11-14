"""Theme templates, the two token layouts, and the QSS re-tint table.

The loader is deliberately Qt-free: everything here is checked without an
application object, so a template regression fails fast instead of surfacing as
a half-painted window.
"""
from __future__ import annotations

import json

import pytest

from core import ui_theme
from core.ui_theme import C


@pytest.fixture(autouse=True)
def _restore_palette():
    """``C`` is a process-wide singleton; leave it as we found it."""
    saved = {key: value for key, value in vars(C).items() if key.isupper()}
    saved_meta = (C.current_slug, C.current_name, C.current_variant, C.current_value)
    yield
    for key, value in saved.items():
        setattr(C, key, value)
    C.current_slug, C.current_name, C.current_variant, C.current_value = saved_meta


def _brahma(layout: str) -> dict:
    """The Quantum Dark document rewritten into ``layout``."""
    if layout == "canonical":
        return {
            "canvas": {"base": "#060709", "surface_1": "#08090C"},
            "border": {"subtle": "rgba(255,255,255,0.08)", "active": "rgba(0,229,255,0.35)"},
            "palette": {"accent_primary": "#00E5FF", "accent_secondary": "#8B5CF6"},
        }
    return {
        "bhitti": {"base": "#060709", "surface_1": "#08090C"},
        "citra": {"border_subtle": "rgba(255,255,255,0.08)", "border_active": "rgba(0,229,255,0.35)"},
        "ranga": {"accent_primary": "#00E5FF", "accent_reason": "#8B5CF6"},
    }


def test_the_fork_layout_normalises_onto_the_canonical_one():
    assert ui_theme.normalize_theme_tokens(_brahma("fork")) == ui_theme.normalize_theme_tokens(
        _brahma("canonical")
    )


def test_unknown_groups_and_keys_survive():
    tokens = ui_theme.normalize_theme_tokens(
        {"canvas": {"base": "#000000", "future_token": "#123456"}, "extra": {"x": 1}}
    )

    assert tokens["canvas"]["future_token"] == "#123456"
    assert tokens["extra"] == {"x": 1}


def test_tokens_must_be_a_mapping():
    with pytest.raises(TypeError):
        ui_theme.normalize_theme_tokens(["canvas"])


def test_a_document_needs_a_slug():
    with pytest.raises(ValueError):
        ui_theme.theme_from_dict({"tokens": _brahma("canonical")})


def test_a_document_needs_tokens():
    with pytest.raises(ValueError):
        ui_theme.theme_from_dict({"slug": "empty"})


def test_every_shipped_template_parses_and_slugs_are_unique():
    themes = ui_theme.load_theme_templates()

    assert len(themes) == 8
    slugs = [theme["slug"] for theme in themes]
    assert len(set(slugs)) == len(slugs)


def test_both_shipped_layouts_describe_the_same_brahma_palette():
    """The four fork files are the Brahma ones under another spelling."""
    directory = ui_theme.template_dir()
    upstream = json.loads((directory / "brahma-quantum-dark.json").read_text(encoding="utf-8"))
    fork = json.loads((directory / "canonical-citra.json").read_text(encoding="utf-8"))

    assert ui_theme.normalize_theme_tokens(upstream["tokens"]) == ui_theme.normalize_theme_tokens(
        fork["tokens"]
    )


def test_a_malformed_template_is_skipped_not_fatal(tmp_path):
    (tmp_path / "good.json").write_text(
        json.dumps({"slug": "good", "tokens": _brahma("canonical")}), encoding="utf-8"
    )
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    assert [theme["slug"] for theme in ui_theme.load_theme_templates(tmp_path)] == ["good"]


def test_a_missing_directory_yields_no_templates(tmp_path):
    assert ui_theme.load_theme_templates(tmp_path / "nope") == []


def test_the_legacy_hex_path_is_unchanged():
    C.load_theme("#ffffff")

    assert C.PRI == "#ffffff"
    # A white accent has always fallen back to the built-in cyan pair.
    assert C.ACC == "#00e5ff"
    assert C.ACC2 == "#80ffff"
    assert C.BORDER == "rgba(255, 255, 255, 0.15)"


def test_the_legacy_named_path_still_maps_words():
    C.load_theme("blue")

    assert C.PRI == "#007aff"
    assert C.ACC == "#007aff"


def test_an_unknown_word_falls_back_to_white():
    C.load_theme("Gold")

    assert C.PRI == "#ffffff"
    assert C.ACC == "#00e5ff"


def test_a_slug_loads_the_template_and_records_it():
    assert C.apply_theme_value("brahma-paper-light") is True

    assert C.current_slug == "brahma-paper-light"
    assert C.current_variant == "light"
    assert C.current_name == "Brahma Paper Light"


def test_a_slug_and_its_hex_are_both_accepted():
    C.apply_theme_value("canonical-citra")
    from_template = C.ACC

    C.apply_theme_value(from_template)

    assert C.current_slug == ""
    assert C.ACC == from_template


def test_a_template_token_reaches_its_palette_attribute():
    C.apply_theme_value("canonical-citra")

    # accent_primary of the shipped Citra template.
    assert C.ACC.upper() == "#00E5FF"
    assert C.PANEL == "#08090C"


def test_an_omitted_group_does_not_inherit_the_previous_theme():
    C.apply_theme_value("brahma-quantum-dark")
    C.load_theme("#ff3b30")
    C.load_theme_tokens({"slug": "minimal", "tokens": {"palette": {"accent_primary": "#112233"}}})

    assert C.ACC == "#112233"
    assert C.GREEN == "#37ff5f"
    # The hex theme had painted every border red; a template without a border
    # group must fall back to the built-in default, not keep that.
    assert C.BORDER == "rgba(0, 229, 255, 0.16)"
    assert C.BG == "#020305"


def test_retint_rewrites_the_legacy_literals():
    C.load_theme("#ffffff")
    style = "color: #ffd700; border: 1px solid #00b4d8; background: rgba(255, 179, 0, 0.2);"

    out = C.retint(style)

    assert "#ffd700" not in out
    assert "#00b4d8" not in out
    assert "255, 179, 0" not in out
    assert "0, 229, 255" in out


def test_retint_derives_rgba_from_the_live_accent():
    C.load_theme("#ff3b30")

    out = C.retint("background: rgba(255, 179, 0, 0.2);")

    assert "rgba(255, 59, 48, 0.2)" in out


def test_retint_is_idempotent_for_the_default_palette():
    C.load_theme("#ffffff")

    once = C.retint("color: #ffd700; box-shadow: 0 0 4px #00e5ff;")

    assert C.retint(once) == once


def test_contrasting_text_picks_readable_ink():
    assert ui_theme.contrasting_text("#ffffff") == "#0b0c10"
    assert ui_theme.contrasting_text("#020305") == "#ffffff"
    assert ui_theme.contrasting_text("not a colour") == "#ffffff"


def test_parse_hex_rejects_junk():
    assert ui_theme.parse_hex("#00E5FF") == (0, 229, 255)
    assert ui_theme.parse_hex("#00e5f") is None
    assert ui_theme.parse_hex(None) is None
    assert ui_theme.parse_hex("rgba(0,0,0,0.5)") is None
