"""MainWindow's _NavMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from core.ui_theme import (C)


def bind(*, _LEFT_W, _RIGHT_W):
    globals().update(_LEFT_W=_LEFT_W, _RIGHT_W=_RIGHT_W)


class _NavMixin:
    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_left_sidebar(self):
        self._left_collapsed = not self._left_collapsed
        self._apply_sidebar_state()

    def _toggle_right_sidebar(self):
        self._right_collapsed = not self._right_collapsed
        self._apply_sidebar_state()
        if not self._right_collapsed and hasattr(self, "_inline_workspace"):
            if hasattr(self._inline_workspace, "focus_input"):
                self._inline_workspace.focus_input()

    def _apply_sidebar_state(self):
        if hasattr(self, "_left_content"):
            self._left_content.setVisible(not self._left_collapsed)
            if hasattr(self, "_left_panel"):
                self._left_panel.setFixedWidth(56 if self._left_collapsed else _LEFT_W)
            if hasattr(self, "_left_toggle_btn"):
                self._left_toggle_btn.setText(">" if self._left_collapsed else "<")
                self._left_toggle_btn.setToolTip("Expand left sidebar" if self._left_collapsed else "Collapse left sidebar")
        if hasattr(self, "_right_panel"):
            if self._right_collapsed:
                self._right_panel.setVisible(False)
            else:
                self._right_panel.setVisible(True)
                self._right_panel.setFixedWidth(_RIGHT_W)
        if hasattr(self, "_right_content"):
            self._right_content.setVisible(not self._right_collapsed)
        if hasattr(self, "_right_stack"):
            self._right_stack.setVisible(not self._right_collapsed)
        if hasattr(self, "_btn_chat"):
            if not getattr(self, "_right_collapsed", False):
                self._btn_chat.setStyleSheet(
                    f"QPushButton {{ background: rgba(0, 229, 255, 0.18); color: {C.PRI}; border: 1px solid {C.PRI}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }}"
                )
            else:
                self._btn_chat.setStyleSheet(
                    f"QPushButton {{ background: rgba(12,14,18,200); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }} QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI}; }}"
                )
        if hasattr(self, "_center_panel"):
            self._center_panel.update()
        self.resizeEvent(None)

    def _on_nav_dashboard(self):
        if hasattr(self, "_center_stack"):
            self._center_stack.setCurrentIndex(0)
        self._update_nav_styles()

    def _on_nav_settings(self):
        if hasattr(self, "_center_stack"):
            self._center_stack.setCurrentIndex(4)
        self._update_nav_styles()

    def _update_nav_styles(self):
        cur_idx = self._center_stack.currentIndex() if hasattr(self, "_center_stack") else 0
        if hasattr(self, "_btn_dashboard"):
            if cur_idx == 0:
                self._btn_dashboard.setStyleSheet(
                    f"QPushButton {{ background: rgba(0, 229, 255, 0.18); color: {C.PRI}; border: 1px solid {C.PRI}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }}"
                )
            else:
                self._btn_dashboard.setStyleSheet(
                    f"QPushButton {{ background: rgba(12,14,18,200); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }} QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI}; }}"
                )
        if hasattr(self, "_btn_settings"):
            if cur_idx in (3, 4):
                self._btn_settings.setStyleSheet(
                    f"QPushButton {{ background: rgba(0, 229, 255, 0.18); color: {C.PRI}; border: 1px solid {C.PRI}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }}"
                )
            else:
                self._btn_settings.setStyleSheet(
                    f"QPushButton {{ background: rgba(12,14,18,200); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }} QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI}; }}"
                )
