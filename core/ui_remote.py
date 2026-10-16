"""MainWindow's _RemoteMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtCore import (Qt)


def bind(*, RemoteKeyOverlay):
    globals().update(RemoteKeyOverlay=RemoteKeyOverlay)


class _RemoteMixin:
    def show_app(self):
        try:
            self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        except Exception:
            pass
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_remote_connect(self):
        if not self.on_remote_clicked:
            self._log_sig.emit("ERR: Mobile remote is not ready yet.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log_sig.emit("ERR: Mobile remote could not start. Install dashboard dependencies and try again.")
            return
        url = result[0]
        key = result[1]
        auto = result[2] if len(result) >= 3 else url
        manual = result[3] if len(result) >= 4 else url

        if self._remote_overlay is not None:
            try:
                self._remote_overlay.deleteLater()
            except Exception:
                pass
            self._remote_overlay = None

        overlay = RemoteKeyOverlay(url, key, auto, manual, parent=self)
        overlay.set_new_key_callback(self.on_remote_clicked)
        overlay.closed.connect(lambda: setattr(self, "_remote_overlay", None))
        self._remote_overlay = overlay
        self._position_remote_overlay()
        overlay.show()
        overlay.raise_()
        self._log_sig.emit(f"SYS: Mobile Connect ready at {manual}. Scan the QR code or enter key {key}.")

    def _position_remote_overlay(self):
        if not self._remote_overlay:
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._remote_overlay.width()) // 2)
        y = max(16, (geo.height() - self._remote_overlay.height()) // 2)
        self._remote_overlay.move(x, y)

    def notify_phone_connected(self):
        if self._remote_overlay is not None:
            self._remote_overlay.mark_connected()
        self._log_sig.emit("SYS: Phone connected to Brahma Evo remote.")
