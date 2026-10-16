"""BrahmaUI's _ShellSidebarMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations



class _ShellSidebarMixin:
    def _toggle_command_bar(self):
        if self._command_bar.isVisible():
            self._command_bar.hide()
        else:
            self._command_bar.show_near(self._launcher)

    def _toggle_workspace_sidebar(self):
        if self._workspace_sidebar.isVisible() and not self._workspace_sidebar.is_collapsed():
            self._close_workspace_sidebar()
        else:
            self._show_workspace_sidebar()

    def _show_workspace_sidebar(self):
        self._workspace_sidebar.show_workspace()
        self._workspace_sidebar.focus_input()
        if self._launcher.isVisible():
            self._launcher.raise_()

    def _close_workspace_sidebar(self):
        self._workspace_sidebar.hide_workspace()
        if self._launcher.isVisible():
            self._launcher.raise_()

    def _show_floating_icon(self):
        launcher_pos = self._load_app_settings().get("launcher_pos")
        if isinstance(launcher_pos, (list, tuple)) and len(launcher_pos) == 2:
            try:
                self._launcher.show_at(int(launcher_pos[0]), int(launcher_pos[1]))
                return
            except Exception:
                pass
        self._launcher.show_at()
