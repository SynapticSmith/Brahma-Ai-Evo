"""MainWindow's _WiringMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtWidgets import (QStackedWidget)


class _WiringMixin:
    def set_settings_bridge(self, bridge):
        self._settings_bridge = bridge
        if hasattr(self, "_settings_page") and hasattr(self._settings_page, "set_controller"):
            self._settings_page.set_controller(bridge)
        if hasattr(self, "_settings_sidebar") and hasattr(self._settings_sidebar, "set_controller"):
            self._settings_sidebar.set_controller(bridge)
        self._set_page(self._current_page)

    def _set_page(self, page: str):
        self._current_page = page
        if hasattr(self, "_center_stack") and isinstance(self._center_stack, QStackedWidget):
            index = {"dashboard": 0, "home": 1, "devices": 2, "settings": 3}.get(page, 0)
            self._center_stack.setCurrentIndex(index)
        if page == "devices" and hasattr(self, "_devices_page"):
            try:
                self._devices_page.refresh()
            except Exception:
                pass
        if page == "home" and hasattr(self, "_home_page"):
            try:
                self._home_page.refresh()
            except Exception:
                pass
        if page == "dashboard" and hasattr(self, "_smart_devices_section"):
            try:
                self._smart_devices_section.refresh(force=True)
            except Exception:
                pass
        if hasattr(self, "_right_panel"):
            self._right_panel.setVisible(page == "dashboard")
        if hasattr(self, "_right_stack") and isinstance(self._right_stack, QStackedWidget):
            self._right_stack.setCurrentIndex({"dashboard": 0, "settings": 1, "home": 2}.get(page, 2))
            self._right_stack.setVisible(page == "dashboard")
        if self._settings_bridge and hasattr(self._settings_bridge, "set_dashboard_page"):
            try:
                self._settings_bridge.set_dashboard_page(page == "dashboard")
            except Exception:
                pass
        if page == "settings" and hasattr(self, "_settings_page"):
            try:
                self._settings_page.refresh()
            except Exception:
                pass
        if page == "settings" and hasattr(self, "_settings_sidebar"):
            try:
                self._settings_sidebar.refresh()
            except Exception:
                pass

    def set_brahma_connect_service(self, service):
        self._brahma_connect = service
        if hasattr(self, "_devices_page"):
            self._devices_page.set_service(service)
            if service is not None:
                self._devices_page.refresh(force=True)
