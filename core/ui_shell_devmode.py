"""BrahmaUI's _ShellDevModeMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog)


def bind(*, DeveloperModeDialog):
    globals().update(DeveloperModeDialog=DeveloperModeDialog)


class _ShellDevModeMixin:
    def _open_developer_mode_dialog(self):
        try:
            dialog = DeveloperModeDialog(self._win, settings=self._load_app_settings())
            if dialog.exec() == QDialog.DialogCode.Accepted:
                settings = dialog.get_settings()
                self._save_app_settings(settings)
                self._apply_developer_mode_ui()
        except Exception:
            pass

    def _apply_developer_mode_ui(self):
        try:
            settings = self._load_app_settings()
            enabled = bool(settings.get("developer_mode_enabled", False))
            workspace = str(settings.get("developer_mode_workspace", "")).strip()
            if hasattr(self._win, "_developer_card") and hasattr(self._win, "_developer_status_lbl"):
                if enabled:
                    workspace_text = workspace or "No folder selected"
                    self._win._developer_status_lbl.setText(
                        f"Developer mode is on • {workspace_text}"
                    )
                # Keep legacy card permanently hidden; modern UI integrates status inside settings
                self._win._developer_card.hide()
        except Exception:
            pass
