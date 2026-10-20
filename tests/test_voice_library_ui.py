"""UI tests for the speech card's local-voice pickers and the Voice Library.

Offscreen Qt (same trick as test_spotify_settings_card.py): the assertions are
about wiring -- which widgets exist, which rows appear for which engine, and
whether a selection reaches core/speech_tts.py -- not about pixels.

No network and no writes to the real config: the catalog is stubbed and the
settings setters are pointed at a recorder.
"""
import os

import pytest


@pytest.fixture
def app(tmp_path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def recorded_settings(monkeypatch):
    """Capture what speech_tts reads/writes instead of touching the real file."""
    from core import speech_tts

    written = {}
    monkeypatch.setattr(speech_tts, "_set_setting", lambda k, v: written.__setitem__(k, v))
    monkeypatch.setattr(
        speech_tts, "_setting", lambda k, d=None: written.get(k, d)
    )
    return written


def _install_piper_voice(root, key):
    piper = root / "BrahmaAI" / "voices" / "piper"
    piper.mkdir(parents=True, exist_ok=True)
    (piper / f"{key}.onnx").write_bytes(b"model")
    (piper / f"{key}.onnx.json").write_bytes(b"{}")


def _install_kokoro(root, *names):
    kokoro = root / "BrahmaAI" / "voices" / "kokoro"
    kokoro.mkdir(parents=True, exist_ok=True)
    for name in names:
        (kokoro / name).write_bytes(b"data")


def _page(app, tmp_path, monkeypatch, read_from=None):
    """Build the System & Connect page with deterministic engine reads.

    `read_from` (the `recorded_settings` dict) makes speech_tts reads resolve
    against the test's own store; without it, reads return their defaults.
    """
    from core import speech_tts

    monkeypatch.setattr(
        speech_tts, "_setting",
        (lambda k, d=None: read_from.get(k, d)) if read_from is not None
        else (lambda k, d=None: d),
    )

    import ui

    page = ui.SystemConnectivityPage()
    page.show()
    app.processEvents()
    return page


def _wait_until(app, predicate, timeout=5.0):
    """Pump the Qt event loop until a worker-thread result has landed."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_speech_card_exposes_the_voice_pickers_and_library_button(
    app, tmp_path, monkeypatch, recorded_settings
):
    page = _page(app, tmp_path, monkeypatch, recorded_settings)

    for attr in ("_piper_voice_box", "_kokoro_model_box", "_kokoro_voice_box"):
        assert hasattr(page, attr), f"speech card is missing {attr}"
    assert hasattr(page, "_on_voice_library")
    assert hasattr(page, "_refresh_local_voice_widgets")


def test_piper_row_is_shown_only_for_the_piper_engine(app, tmp_path, monkeypatch, recorded_settings):
    _install_piper_voice(tmp_path, "en_US-amy-medium")
    _install_kokoro(tmp_path, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    page = _page(app, tmp_path, monkeypatch, recorded_settings)

    # Selecting a combo entry fires the card's persist signal, which writes the
    # real settings file; block that so the test only exercises visibility.
    box = page._tts_box
    box.blockSignals(True)
    idx_piper = box.findData("piper")
    idx_kokoro = box.findData("kokoro")
    assert idx_piper >= 0 and idx_kokoro >= 0

    box.setCurrentIndex(idx_piper)
    page._refresh_local_voice_widgets()
    app.processEvents()
    assert page._piper_voice_box.isVisible() is True
    assert page._kokoro_model_box.isVisible() is False
    assert page._kokoro_voice_box.isVisible() is False

    box.setCurrentIndex(idx_kokoro)
    page._refresh_local_voice_widgets()
    app.processEvents()
    assert page._piper_voice_box.isVisible() is False
    assert page._kokoro_model_box.isVisible() is True
    assert page._kokoro_voice_box.isVisible() is True


def test_pickers_list_what_is_installed(app, tmp_path, monkeypatch, recorded_settings):
    _install_piper_voice(tmp_path, "en_US-amy-medium")
    _install_piper_voice(tmp_path, "en_US-lessac-medium")
    _install_kokoro(tmp_path, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    page = _page(app, tmp_path, monkeypatch, recorded_settings)
    page._refresh_local_voice_widgets()

    piper_ids = [page._piper_voice_box.itemData(i) for i in range(page._piper_voice_box.count())]
    assert "" in piper_ids, "an auto-discover option must remain"
    assert {"en_US-amy-medium", "en_US-lessac-medium"} <= set(piper_ids)

    models = [page._kokoro_model_box.itemData(i) for i in range(page._kokoro_model_box.count())]
    assert "kokoro-v1.0.onnx" in models

    voices = [page._kokoro_voice_box.itemText(i) for i in range(page._kokoro_voice_box.count())]
    assert "af_heart" in voices, "the pack's voices must be offered by name"


def test_selecting_a_piper_voice_reaches_the_engine(app, tmp_path, monkeypatch, recorded_settings):
    _install_piper_voice(tmp_path, "en_US-amy-medium")
    _install_piper_voice(tmp_path, "en_US-lessac-medium")
    page = _page(app, tmp_path, monkeypatch, recorded_settings)
    page._refresh_local_voice_widgets()

    idx = page._piper_voice_box.findData("en_US-lessac-medium")
    assert idx >= 0
    page._piper_voice_box.setCurrentIndex(idx)  # fires _on_piper_voice_changed

    from core import speech_tts

    assert recorded_settings["speech_tts_piper_voice"] == "en_US-lessac-medium"
    assert speech_tts.get_piper_voice() == "en_US-lessac-medium"
    # The legacy path override is cleared so the picker is what is spoken.
    assert recorded_settings["speech_tts_voice"] == ""


def test_selecting_a_kokoro_model_selects_its_pack(app, tmp_path, monkeypatch, recorded_settings):
    _install_kokoro(
        tmp_path,
        "kokoro-v1.0.onnx", "kokoro-v1.1-zh.onnx",
        "voices-v1.0.bin", "voices-v1.1-zh.bin",
    )
    page = _page(app, tmp_path, monkeypatch, recorded_settings)
    page._refresh_local_voice_widgets()

    idx = page._kokoro_model_box.findData("kokoro-v1.1-zh.onnx")
    assert idx >= 0
    page._kokoro_model_box.setCurrentIndex(idx)

    assert recorded_settings["speech_tts_kokoro_model"] == "kokoro-v1.1-zh.onnx"
    assert recorded_settings["speech_tts_kokoro_voices"] == "voices-v1.1-zh.bin", (
        "picking a model must bring its matching voice pack along"
    )


# ---------------------------------------------------------------------------
# The dialog itself
# ---------------------------------------------------------------------------
def _dialog(app, tmp_path, monkeypatch, piper_entries=()):
    from core import voice_catalog
    from core.ui_voice_library import VoiceLibraryDialog

    monkeypatch.setattr(
        voice_catalog, "piper_catalog",
        lambda force_refresh=False, timeout=25.0: (list(piper_entries), ""),
    )
    dialog = VoiceLibraryDialog(None)
    dialog.show()
    app.processEvents()
    _wait_until(app, lambda: not dialog._loading)
    app.processEvents()
    return dialog


def test_dialog_builds_and_lists_the_piper_catalog(app, tmp_path, monkeypatch):
    entries = [
        {
            "engine": "piper", "id": "en_US-amy-medium", "kind": "model",
            "name": "amy", "language_code": "en_US", "language": "English",
            "quality": "medium", "size_bytes": 63201294, "size_label": "60.3 MB",
            "filename": "en_US-amy-medium.onnx",
            "extra_filename": "en_US-amy-medium.onnx.json",
            "url": "", "extra_url": "", "md5": "", "sha256": "",
            "warning": "", "installed": False,
        },
        {
            "engine": "piper", "id": "de_DE-thorsten-low", "kind": "model",
            "name": "thorsten", "language_code": "de_DE", "language": "German",
            "quality": "low", "size_bytes": 63104526, "size_label": "60.2 MB",
            "filename": "de_DE-thorsten-low.onnx",
            "extra_filename": "de_DE-thorsten-low.onnx.json",
            "url": "", "extra_url": "", "md5": "", "sha256": "",
            "warning": "", "installed": False,
        },
    ]
    dialog = _dialog(app, tmp_path, monkeypatch, piper_entries=entries)

    assert dialog._piper_tree.topLevelItemCount() == 2

    # The language filter narrows the list.
    idx = dialog._piper_lang.findData("de_DE")
    assert idx >= 0
    dialog._piper_lang.setCurrentIndex(idx)
    app.processEvents()
    assert dialog._piper_tree.topLevelItemCount() == 1
    assert dialog._piper_tree.topLevelItem(0).text(0) == "de_DE-thorsten-low"

    # So does the search box.
    dialog._piper_lang.setCurrentIndex(0)
    dialog._piper_search.setText("amy")
    app.processEvents()
    assert dialog._piper_tree.topLevelItemCount() == 1
    assert dialog._piper_tree.topLevelItem(0).text(0) == "en_US-amy-medium"

    # Nothing is downloaded yet, so "use" is not offered.
    dialog._piper_search.setText("")
    app.processEvents()
    dialog._piper_tree.setCurrentItem(dialog._piper_tree.topLevelItem(0))
    assert dialog._piper_use.isEnabled() is False


def test_dialog_lists_kokoro_models_and_packs(app, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt

    _install_kokoro(tmp_path, "kokoro-v1.0.onnx", "voices-v1.0.bin")
    dialog = _dialog(app, tmp_path, monkeypatch)

    models = [
        dialog._kokoro_models.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
        for i in range(dialog._kokoro_models.topLevelItemCount())
    ]
    assert "kokoro-v1.0.onnx" in models
    assert dialog._kokoro_bins.topLevelItemCount() == 2


def test_dialog_reports_an_offline_catalog_instead_of_hanging(app, tmp_path, monkeypatch):
    from core import voice_catalog

    monkeypatch.setattr(
        voice_catalog, "piper_catalog",
        lambda force_refresh=False, timeout=25.0: ([], "Could not download the Piper voice list: offline"),
    )
    from core.ui_voice_library import VoiceLibraryDialog

    dialog = VoiceLibraryDialog(None)
    assert _wait_until(app, lambda: not dialog._loading), (
        "the catalog worker must finish and hand control back"
    )
    assert dialog._piper_tree.topLevelItemCount() == 0
    assert "offline" in dialog._op_label.text()


def test_busy_state_disables_the_actions(app, tmp_path, monkeypatch):
    dialog = _dialog(app, tmp_path, monkeypatch)
    dialog._set_busy(True)
    app.processEvents()
    assert dialog._piper_download.isEnabled() is False
    assert dialog._cancel_btn.isEnabled() is True
    dialog._set_busy(False)
    app.processEvents()
    assert dialog._piper_download.isEnabled() is True
    assert dialog._cancel_btn.isEnabled() is False
