"""MainWindow's _MeetingMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtWidgets import (QApplication)


def bind(*, IncomingAlertDialog):
    globals().update(IncomingAlertDialog=IncomingAlertDialog)


class _MeetingMixin:
    def _on_discord_status_update(self, message: str):
        if not message:
            return
        if hasattr(self, "_discord_status_lbl"):
            note = message.strip()
            self._refresh_discord_card(note)
            if self._meeting_overlay is not None:
                try:
                    self._meeting_overlay.raise_()
                except Exception:
                    pass
        else:
            if self._meeting_overlay is not None:
                self._meeting_overlay.hide()
            self._meeting_overlay_collapsed = False

    def _request_stop_meeting(self):
        if self.on_attention_action:
            try:
                self.on_attention_action({"kind": "meeting", "app": "Meeting mode"}, "stop")
            except Exception:
                pass

    def _toggle_meeting_overlay(self):
        if self._meeting_overlay is None:
            return
        self._meeting_overlay_collapsed = not self._meeting_overlay_collapsed
        self._meeting_overlay.set_collapsed(self._meeting_overlay_collapsed)
        self._position_meeting_overlay()
        if not self._meeting_overlay.isVisible():
            self._meeting_overlay.show()
        self._meeting_overlay.raise_()

    def _position_meeting_overlay(self):
        if self._meeting_overlay is None:
            return
        screen = QApplication.primaryScreen().availableGeometry()
        margin = 12
        h = self._meeting_overlay.height()
        w = min(980, max(780, screen.width() - margin * 2))
        x = screen.left() + (screen.width() - w) // 2
        y = screen.top() + margin
        self._meeting_overlay.setGeometry(x, y, w, h)

    def _show_attention_alert(self, event: object):
        data = event if isinstance(event, dict) else {}
        if self._incoming_alert is not None:
            try:
                self._incoming_alert.close()
            except Exception:
                pass
            self._incoming_alert = None

        dlg = IncomingAlertDialog(data, None)
        dlg.decision.connect(lambda decision, ev=data: self._attention_choice(ev, decision))

        if (data.get("kind") or "").strip().lower() == "call":
            self.show_app()

        screen = QApplication.primaryScreen().availableGeometry()
        margin = 16
        dlg.adjustSize()
        x = screen.right() - dlg.width() - margin
        y = screen.top() + margin
        dlg.move(x, y)
        dlg.show()
        dlg.raise_()
        self._incoming_alert = dlg

    def _attention_choice(self, event: dict, decision: str):
        if self.on_attention_action:
            try:
                self.on_attention_action(event, decision)
            except Exception:
                pass
        self._incoming_alert = None
