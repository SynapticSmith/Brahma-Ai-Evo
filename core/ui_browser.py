"""Embedded browser hook for MainWindow, kept out of the UI facade."""
from __future__ import annotations

from core.ui_thread import run_on_ui


class _BrowserMixin:
    def open_embedded_browser(self, address: str):
        from core.embedded_browser import create_embedded_browser_window, normalize_browser_url

        safe_address = normalize_browser_url(address)

        def _open():
            browser_window = getattr(self, "_embedded_browser_window", None)
            if browser_window is None:
                browser_window = create_embedded_browser_window(self)
                self._embedded_browser_window = browser_window
            browser_window.navigate(safe_address)
            browser_window.show()
            browser_window.raise_()
            browser_window.activateWindow()

        run_on_ui(self, _open)
