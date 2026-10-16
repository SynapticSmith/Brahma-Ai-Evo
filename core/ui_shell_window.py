"""BrahmaUI's _ShellWindowMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtCore import (Qt)
from PyQt6.QtGui import (QIcon)


def bind(*, _logo_icon):
    globals().update(_logo_icon=_logo_icon)


class _ShellWindowMixin:
    def _make_app_icon(self) -> QIcon:
        return _logo_icon()

    def set_brahma_connect_service(self, service):
        self._win.set_brahma_connect_service(service)


    

    def show_main(self):
        if self._boot_overlay is not None and not getattr(self._boot_overlay, "_boot_finished", False):
            return
        try:
            self._win.setWindowState(
                (self._win.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
            )
            self._win.showNormal()
        except Exception:
            self._win.show()
        self._win.raise_()
        self._win.activateWindow()
        self._launcher.hide()

    def set_dashboard_page(self, enabled: bool):
        try:
            if not enabled:
                self._command_bar.hide()
                self._workspace_sidebar.hide_workspace(animate=False)
        except Exception:
            pass

    def hide_main(self):
        self._command_bar.hide()
        self._launcher.hide()
        self._win.hide()
