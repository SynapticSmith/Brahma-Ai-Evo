"""Banner-level checks for the ALWAYS ALLOW button.

Kept out of ``test_agent_*.py`` on purpose: the harness CI job installs only
pytest, while this file needs PyQt6. The full suite runs it headlessly.
"""
from __future__ import annotations

import sys

import pytest

PyQt6 = pytest.importorskip("PyQt6.QtWidgets")
QApplication = PyQt6.QApplication
QLabel = PyQt6.QLabel
QPlainTextEdit = PyQt6.QPlainTextEdit
QPushButton = PyQt6.QPushButton

from core.ui_overlays import ConfirmationOverlay  # noqa: E402


_APP = None


def _app():
    # Keep a module-level reference: letting the only reference to a
    # QApplication die at the end of a test tears down Qt while widgets still
    # exist, which aborts the process on Windows.
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv[:1])
    return _APP


def _labels(overlay):
    return {button.text() for button in overlay.findChildren(QPushButton)}


def test_always_button_only_exists_for_session_approvals():
    _app()
    plain = ConfirmationOverlay("Approve run_command?", "detail")
    assert "ALWAYS ALLOW" not in _labels(plain)
    plain.deleteLater()

    agent = ConfirmationOverlay("Approve run_command?", "detail", allow_always=True)
    assert _labels(agent) >= {"CONFIRM", "ALWAYS ALLOW", "CANCEL"}
    agent.deleteLater()


def test_always_button_emits_its_own_signal_once():
    _app()
    overlay = ConfirmationOverlay("Approve?", "detail", allow_always=True)
    answered, always, closed = [], [], []
    overlay.answered.connect(answered.append)
    overlay.always.connect(lambda: always.append(True))
    overlay.closed.connect(lambda: closed.append(True))

    button = next(b for b in overlay.findChildren(QPushButton) if b.text() == "ALWAYS ALLOW")
    button.click()

    assert answered == []
    assert always == [True]
    assert closed == [True]
    overlay.deleteLater()


def test_banner_names_what_always_allow_will_remember():
    _app()
    overlay = ConfirmationOverlay(
        "Run this command?",
        "rule: classes.exec — shell commands need approval.\n\ncommand: pytest -q",
        allow_always=True,
        remember_label="this exact command (pytest -q)",
    )

    texts = [label.text() for label in overlay.findChildren(QLabel)]
    assert any("AGENT APPROVAL REQUIRED" in text for text in texts)
    assert any(
        "remembers this exact command (pytest -q)" in text for text in texts
    )
    # A multi-line detail goes into a scrollable box, not a wall of wrapped text.
    boxes = overlay.findChildren(QPlainTextEdit)
    assert boxes and "command: pytest -q" in boxes[0].toPlainText()
    overlay.deleteLater()

    plain = ConfirmationOverlay("Shut down?", "detail")
    plain_texts = [label.text() for label in plain.findChildren(QLabel)]
    assert any("PHYSICAL CONFIRMATION REQUIRED" in text for text in plain_texts)
    assert not any("ALWAYS ALLOW remembers" in text for text in plain_texts)
    plain.deleteLater()
