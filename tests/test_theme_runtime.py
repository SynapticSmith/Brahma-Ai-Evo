"""Runtime pieces: live re-tint, wing popup timers, boot speed, Settings wiring.

Importing ``ui`` installs the ``QWidget.setStyleSheet`` re-tint patch, so these
tests run offscreen with a real application object.
"""
import os

import pytest

from core.ui_theme import C


@pytest.fixture(scope="session")
def _app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def _ui(_app):
    import ui

    return ui


@pytest.fixture(scope="session")
def page(_ui):
    return _ui.SystemConnectivityPage()


@pytest.fixture(autouse=True)
def _restore_palette():
    saved = {key: value for key, value in vars(C).items() if key.isupper()}
    saved_meta = (C.current_slug, C.current_name, C.current_variant, C.current_value)
    yield
    for key, value in saved.items():
        setattr(C, key, value)
    C.current_slug, C.current_name, C.current_variant, C.current_value = saved_meta


# -- the stylesheet patch -------------------------------------------------

def test_a_stylesheet_keeps_its_pre_tint_form(_ui):
    from PyQt6.QtWidgets import QWidget

    widget = QWidget()
    try:
        widget.setStyleSheet("color: #ffd700;")
        assert widget._brahma_raw_qss == "color: #ffd700;"
        assert "#ffd700" not in widget.styleSheet()
    finally:
        widget.deleteLater()


def test_a_theme_change_repaints_an_existing_widget(_ui):
    from PyQt6.QtWidgets import QWidget

    C.load_theme("#020305")
    widget = QWidget()
    try:
        widget.setStyleSheet("color: #ffd700;")
        C.apply_theme_value("#37ff5f")
        assert "#37ff5f" in widget.styleSheet()
    finally:
        widget.deleteLater()


def test_a_blank_stylesheet_is_left_alone(_ui):
    from PyQt6.QtWidgets import QWidget

    widget = QWidget()
    try:
        widget.setStyleSheet("")
        assert not hasattr(widget, "_brahma_raw_qss")
    finally:
        widget.deleteLater()


# -- popup timers on the wings -------------------------------------------

def test_a_wing_uses_the_configured_dismiss_seconds(_ui, monkeypatch):
    import core.popup_settings as popup
    from core.ui_telemetry import BrahmaTelemetryWing

    monkeypatch.setattr(popup, "_settings", lambda: {"popup_dismiss_seconds": 25})
    wing = BrahmaTelemetryWing()
    try:
        assert wing._refresh_dismiss_budget() is True
        assert wing._total_duration_ms == 25_000
        assert wing._remaining_ms == 25_000
    finally:
        wing.deleteLater()


def test_a_wing_falls_back_to_its_own_ten_seconds(_ui, monkeypatch):
    import core.popup_settings as popup
    from core.ui_telemetry import BrahmaTelemetryWing

    monkeypatch.setattr(popup, "_settings", lambda: {"popup_dismiss_seconds": 0})
    wing = BrahmaTelemetryWing()
    try:
        assert wing._refresh_dismiss_budget() is True
        assert wing._total_duration_ms == int(popup.DEFAULT_TELEMETRY_WING_SECONDS * 1000)
    finally:
        wing.deleteLater()


def test_a_wing_stays_open_when_auto_dismiss_is_off(_ui, monkeypatch):
    import core.popup_settings as popup
    from core.ui_telemetry import BrahmaResultWing

    monkeypatch.setattr(popup, "_settings", lambda: {"popup_auto_dismiss_enabled": False})
    wing = BrahmaResultWing()
    try:
        assert wing._refresh_dismiss_budget() is False
        assert wing._total_duration_ms == 0
        assert not wing._timer.isActive()
        assert wing._fuse_lbl.text() == "∞"
    finally:
        wing.deleteLater()


# -- boot speed -----------------------------------------------------------

def test_the_boot_overlay_honours_its_speed(_ui):
    from core.ui_transients import BOOT_SPEED_SECONDS, BootSequenceOverlay

    overlay = BootSequenceOverlay(speed="Slow")
    try:
        assert overlay._total_duration == BOOT_SPEED_SECONDS["Slow"]
    finally:
        overlay.deleteLater()


def test_an_unknown_speed_keeps_the_historical_duration(_ui):
    from core.ui_transients import BootSequenceOverlay

    overlay = BootSequenceOverlay(speed="warp")
    try:
        assert overlay._total_duration == 3.6
    finally:
        overlay.deleteLater()


# -- Settings wiring ------------------------------------------------------

def test_the_defaults_carry_the_two_new_controls(_ui):
    from core.ui_startup import _default_app_settings

    settings = _default_app_settings()
    assert settings["startup_anim_speed"] == "Normal"
    assert settings["app_theme"] == C.DEFAULT_APP_THEME


def test_the_speed_control_persists(page, monkeypatch):
    written = {}
    monkeypatch.setattr(page, "_set_setting", lambda key, value: written.__setitem__(key, value))

    page._anim_speed.setCurrentText("Slow")

    assert written == {"startup_anim_speed": "Slow"}


def test_the_speed_control_only_persists_a_known_value(page, monkeypatch):
    written = {}
    monkeypatch.setattr(page, "_set_setting", lambda key, value: written.__setitem__(key, value))

    page._on_animation_speed_changed("warp")

    assert written == {"startup_anim_speed": "Normal"}


def test_picking_a_template_applies_it_without_a_restart(page, monkeypatch):
    written = {}
    monkeypatch.setattr(page, "_set_setting", lambda key, value: written.__setitem__(key, value))
    monkeypatch.setattr(page, "_load_app_settings", lambda: {"app_theme": "#ffffff"})

    index = page._theme_combo.findData("brahma-slate-frost")
    assert index > 0, "the bundled templates should be listed"

    page._theme_combo.setCurrentIndex(index)

    assert written == {"app_theme": "brahma-slate-frost"}
    assert C.current_slug == "brahma-slate-frost"
    assert page._pick_theme_btn.isEnabled()


def test_the_template_combo_offers_a_custom_entry(page):
    assert page._theme_combo.itemData(0) == ""


def test_a_theme_change_reaches_real_widgets(page):
    """The Settings page is built, so the walker must find something to repaint."""
    C.apply_theme_value("#37ff5f")

    assert C.reapply_all() > 0


def test_installing_the_patch_twice_does_not_double_wrap(_ui):
    from PyQt6.QtWidgets import QWidget

    before = QWidget.setStyleSheet

    C.install_stylesheet_patch(QWidget)

    assert QWidget.setStyleSheet is before
