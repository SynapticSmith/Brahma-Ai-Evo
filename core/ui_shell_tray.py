"""BrahmaUI's _ShellTrayMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtWidgets import (QMenu, QSystemTrayIcon)
from core.ui_theme import (C)


class _ShellTrayMixin:
    def _build_tray_menu(self) -> QMenu:
        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{
                background: rgba(8, 8, 8, 245);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{
                padding: 8px 18px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: rgba(255,255,255,0.08);
            }}
        """)

        open_app_action = menu.addAction("Open App")
        open_action = menu.addAction("Open Workspace")
        close_action = menu.addAction("Close Workspace")
        startup_action = menu.addAction("Show Workspace On Startup")
        startup_action.setCheckable(True)
        startup_action.setChecked(bool(self._load_app_settings().get("show_workspace_on_startup", False)))
        show_icon_action = menu.addAction("Show Floating Icon")
        hide_icon_action = menu.addAction("Hide Floating Icon")
        menu.addSeparator()
        resume_session_action = menu.addAction("Resume Agent Session\u2026")
        menu.addSeparator()
        restart_action = menu.addAction("Restart")
        quit_action = menu.addAction("Quit")

        open_app_action.triggered.connect(self.show_main)
        open_action.triggered.connect(self._show_workspace_sidebar)
        close_action.triggered.connect(self._close_workspace_sidebar)
        startup_action.triggered.connect(lambda: self._toggle_workspace_on_startup(startup_action.isChecked()))
        show_icon_action.triggered.connect(self._show_floating_icon)
        hide_icon_action.triggered.connect(self._hide_launcher_with_protection)
        resume_session_action.triggered.connect(lambda: self._submit_command("/resume"))
        restart_action.triggered.connect(self._restart_app)
        quit_action.triggered.connect(self._app.quit)
        return menu

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_workspace_sidebar()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_main()

    def _on_minimized(self):
        self._command_bar.hide()
        self._show_floating_icon()

    def _on_restored(self):
        self._launcher.hide()

    def _on_launcher_double_clicked(self):
        self._workspace_sidebar.hide_workspace(animate=False)
        self.show_main()
