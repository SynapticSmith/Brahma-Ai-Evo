"""MainWindow's _SecondaryOverlaysMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)


def bind(*, MemoryInspectorOverlay):
    globals().update(MemoryInspectorOverlay=MemoryInspectorOverlay)


class _SecondaryOverlaysMixin:
    def _apply_circuit_overlay(self, circuit_data: dict):
        if self._circuit_overlay:
            self._circuit_overlay.deleteLater()
            self._circuit_overlay = None

        from core.circuit_hud import CircuitPopupOverlay

        parent = self.centralWidget()
        overlay = CircuitPopupOverlay(circuit_data or {}, parent=parent)
        overlay.closed.connect(lambda: setattr(self, "_circuit_overlay", None))
        self._circuit_overlay = overlay
        overlay.move(max(8, (parent.width() - overlay.width()) // 2), max(8, (parent.height() - overlay.height()) // 2))
        overlay.show()
        overlay.raise_()

    def _apply_call_screening(self, payload: dict):
        payload = dict(payload or {})
        action = payload.get("action", "show")

        if action == "hide":
            if self._call_screening_dialog:
                self._call_screening_dialog.close()
            self._call_screening_dialog = None
            self._call_screening_transcript = None
            return

        if action == "transcript":
            if self._call_screening_transcript:
                speaker = str(payload.get("speaker", "Caller"))
                text = str(payload.get("text", ""))
                current = self._call_screening_transcript.text()
                self._call_screening_transcript.setText((current + "\n" if current else "") + f"{speaker}: {text}")
            return

        if self._call_screening_dialog:
            self._call_screening_dialog.close()

        dialog = QDialog(self)
        dialog.setWindowTitle("Brahma Evo Call Screening")
        dialog.setModal(False)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        caller = str(payload.get("caller", "Caller"))
        app_name = str(payload.get("app", "Phone / Call"))
        status = str(payload.get("status", "Screening call"))
        title = QLabel(f"{caller}  ·  {app_name}")
        title.setWordWrap(True)
        detail = QLabel(status)
        transcript = QLabel("")
        transcript.setWordWrap(True)
        transcript.setMinimumHeight(70)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(transcript)

        buttons = QHBoxLayout()
        take_over = QPushButton("Take Over")
        hang_up = QPushButton("Hang Up")
        buttons.addWidget(take_over)
        buttons.addWidget(hang_up)
        layout.addLayout(buttons)
        take_over.clicked.connect(self._take_over_screened_call)
        hang_up.clicked.connect(self._hang_up_screened_call)

        self._call_screening_dialog = dialog
        self._call_screening_transcript = transcript
        dialog.finished.connect(lambda: setattr(self, "_call_screening_dialog", None))
        dialog.show()
        dialog.raise_()

    def _take_over_screened_call(self):
        from actions.call_assistant import take_over_active_call
        take_over_active_call()

    def _hang_up_screened_call(self):
        from actions.call_assistant import hang_up_active_call
        hang_up_active_call()

    def _on_confirm_answered(self, accepted: bool):
        try:
            from core import confirm as confirm_gate
            confirm_gate.resolve(accepted)
        except Exception:
            pass
        self._apply_confirm_overlay("hide", "", "")

    def _position_confirm_overlay(self):
        if not getattr(self, "_confirm_overlay", None):
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._confirm_overlay.width()) // 2)
        y = max(16, (geo.height() - self._confirm_overlay.height()) // 2)
        self._confirm_overlay.move(x, y)

    def show_memory_inspector(self):
        self._memory_overlay_sig.emit("show")

    def hide_memory_inspector(self):
        self._memory_overlay_sig.emit("hide")

    def _apply_memory_overlay(self, action: str):
        if action == "hide":
            if getattr(self, "_memory_overlay", None):
                try:
                    self._memory_overlay.deleteLater()
                except Exception:
                    pass
                self._memory_overlay = None
            return

        if action == "show":
            if getattr(self, "_memory_overlay", None):
                try:
                    self._memory_overlay.deleteLater()
                except Exception:
                    pass
                self._memory_overlay = None

            overlay = MemoryInspectorOverlay(parent=self)
            overlay.closed.connect(lambda: setattr(self, "_memory_overlay", None))
            self._memory_overlay = overlay
            self._position_memory_overlay()
            overlay.show()
            overlay.raise_()

    def _position_memory_overlay(self):
        if not getattr(self, "_memory_overlay", None):
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._memory_overlay.width()) // 2)
        y = max(16, (geo.height() - self._memory_overlay.height()) // 2)
        self._memory_overlay.move(x, y)
