"""Regression tests for stylesheet string assembly (review item 674).

``_soft_button_style`` built a QPushButton sheet by concatenating an f-string
with a plain (non-f) string. Brace escaping (``{{`` / ``}}``) only applies
inside f-strings, so the plain line emitted a literal ``}}`` and produced an
unbalanced sheet that Qt rejected at runtime with::

    Could not parse stylesheet of object QPushButton(0x...)

These tests pin the assembled strings to a balanced, un-escaped form so the
bug cannot silently return.
"""
from smart_home_page_new import _panel_style, _soft_button_style


def _assert_balanced(style: str, *, name: str) -> None:
    assert "{{" not in style, f"{name}: stray '{{{{' in stylesheet"
    assert "}}" not in style, f"{name}: stray '}}}}' in stylesheet"
    assert style.count("{") == style.count("}"), f"{name}: unbalanced braces"


def test_soft_button_style_is_balanced():
    style = _soft_button_style()
    _assert_balanced(style, name="_soft_button_style")
    assert "QPushButton {" in style
    assert "QPushButton:hover {" in style


def test_panel_style_is_balanced():
    _assert_balanced(_panel_style(34), name="_panel_style")
