"""Launcher control panel, telemetry/result wings and the stat/log widgets,
moved verbatim out of ui.py (P4-19). Shared project symbols (C, MessageCard)
are injected by ui.py through bind(); Qt and stdlib imports are direct."""
from __future__ import annotations

import math
import os
import sys
import time

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, QTimer, Qt, pyqtSignal)
from PyQt6.QtGui import (QFont)
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)
from core.ui_theme import (C)
from pathlib import (Path)
from sound_manager import (sound_mgr)


def bind(*, MessageCard):
    globals().update(MessageCard=MessageCard)

class LauncherControlPanel(QDialog):
    def __init__(self, *, startup_workspace: bool = False, on_open=None, on_close=None,
                 on_toggle_startup=None, on_hide_icon=None, on_restart=None, on_quit=None,
                 on_open_app=None,
                 on_show_icon=None,
                 on_open_dev=None,
                 on_setting_toggle=None,
                 parent=None):
        super().__init__(parent)
        self._on_setting_toggle = on_setting_toggle
        self._on_open = on_open
        self._on_close = on_close
        self._on_toggle_startup = on_toggle_startup
        self._on_hide_icon = on_hide_icon
        self._on_restart = on_restart
        self._on_quit = on_quit
        self._on_open_app = on_open_app
        self._on_show_icon = on_show_icon
        self._on_open_dev = on_open_dev

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: rgba(15,15,20,235);
                border: 1px solid rgba(0,191,255,60);
                border-radius: 18px;
            }
        """)
        root.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        title = QLabel("BRAHMA EVO CONTROL")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: 1px;")
        lay.addWidget(title)

        sub = QLabel("Desktop launcher controls")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet("color: rgba(255,255,255,0.65); background: transparent;")
        lay.addWidget(sub)

        def mk_btn(text: str, *, checkable: bool = False, checked: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(checkable)
            btn.setChecked(checked)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(36)
            btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,0.05);
                    color: #FFFFFF;
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 12px;
                    padding: 6px 12px;
                    text-align: left;
                }
                QPushButton:hover {
                    background: rgba(0,191,255,0.10);
                    border: 1px solid rgba(0,191,255,0.45);
                }
                QPushButton:checked {
                    background: rgba(0,191,255,0.16);
                    border: 1px solid rgba(0,191,255,0.60);
                }
            """)
            return btn

        self._open_btn = mk_btn("Open Workspace")
        self._close_btn = mk_btn("Close Workspace")
        self._startup_btn = mk_btn("Show Workspace On Startup", checkable=True, checked=bool(startup_workspace))
        self._show_icon_btn = mk_btn("Show Floating Icon")
        self._hide_icon_btn = mk_btn("Hide Floating Icon")
        self._restart_btn = mk_btn("Restart Brahma Evo")
        self._quit_btn = mk_btn("Quit Brahma Evo")
        self._open_app_btn = mk_btn("Open App")
        self._open_dev_btn = mk_btn("Open Developer Mode")

        self._open_btn.clicked.connect(lambda: self._invoke(self._on_open))
        self._close_btn.clicked.connect(lambda: self._invoke(self._on_close))
        self._startup_btn.clicked.connect(lambda: self._invoke(self._on_toggle_startup, self._startup_btn.isChecked()))
        self._show_icon_btn.clicked.connect(lambda: self._invoke(self._on_show_icon))
        self._hide_icon_btn.clicked.connect(self._hide_icon_confirm)
        self._restart_btn.clicked.connect(lambda: self._invoke(self._on_restart))
        self._quit_btn.clicked.connect(lambda: self._invoke(self._on_quit))
        self._open_app_btn.clicked.connect(lambda: self._invoke(self._on_open_app))
        self._open_dev_btn.clicked.connect(lambda: self._invoke(self._on_open_dev))

        for btn in (
            self._open_app_btn, self._open_btn, self._close_btn, self._startup_btn,
            self._show_icon_btn, self._hide_icon_btn, self._open_dev_btn,
            self._restart_btn, self._quit_btn
        ):
            lay.addWidget(btn)

        # --- Live toggles -------------------------------------------------- #
        # These do NOT go through _invoke(), which closes the panel; flipping a
        # provider off and having the panel vanish under the cursor is why the
        # failover chain had no usable switch before.
        self._add_toggle_section(lay, mk_btn)

        self.adjustSize()

    def _add_toggle_section(self, lay, mk_btn):
        """Provider kill switches plus the 3D / memory toggles."""
        try:
            from core import provider_router

            providers = provider_router.all_providers()
        except Exception:
            providers = []

        sep = QLabel("Providers & resources")
        sep.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        sep.setStyleSheet("color: #7fe9ff; background: transparent;")
        lay.addWidget(sep)

        state: dict = {}
        try:
            from memory import config_manager

            state = config_manager.load_settings()
        except Exception:
            state = {}

        for spec in providers:
            key = f"provider_enabled_{spec['id']}"
            enabled = bool(spec["enabled"])
            btn = mk_btn(f"Use {spec['label']}", checkable=True, checked=enabled)
            btn.setToolTip(spec.get("hint", ""))
            btn.toggled.connect(lambda checked, k=key: self._emit_setting(k, checked))
            lay.addWidget(btn)

        anim = mk_btn(
            "3D Background Animation", checkable=True,
            checked=bool(state.get("enable_3d_background", True)),
        )
        anim.setToolTip("Off saves GPU and CPU every frame.")
        anim.toggled.connect(
            lambda checked: self._emit_setting("enable_3d_background", checked)
        )
        lay.addWidget(anim)

        mem = mk_btn(
            "Background Memory Extraction", checkable=True,
            checked=bool(state.get("memory_extraction_enabled", True)),
        )
        mem.setToolTip(
            "Turn off to stop background analysis between messages. "
            "Stays available in Local/Offline mode -- it is never forced off."
        )
        mem.toggled.connect(
            lambda checked: self._emit_setting("memory_extraction_enabled", checked)
        )
        lay.addWidget(mem)

    def _emit_setting(self, key: str, value: bool) -> None:
        """Persist a toggle without closing the panel."""
        if self._on_setting_toggle:
            try:
                self._on_setting_toggle(key, bool(value))
            except Exception:
                pass

    def _invoke(self, fn, *args):
        if fn:
            try:
                fn(*args)
            except Exception:
                pass
        self.close()

    def _hide_icon_confirm(self):
        box = QDialog(self)
        box.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: rgba(15,15,20,240); border: 1px solid rgba(0,191,255,60); border-radius: 16px; }")
        lay.addWidget(frame)
        flay = QVBoxLayout(frame)
        flay.setContentsMargins(18, 16, 18, 16)
        flay.setSpacing(10)
        lbl = QLabel("Hide Brahma Evo icon?")
        lbl.setStyleSheet("color: #FFFFFF; background: transparent; font: 700 11pt 'Segoe UI';")
        sub = QLabel("You can restore it from the system tray.")
        sub.setStyleSheet("color: rgba(255,255,255,0.65); background: transparent;")
        flay.addWidget(lbl)
        flay.addWidget(sub)
        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        hide = QPushButton("Hide")
        for btn in (cancel, hide):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(34)
            btn.setStyleSheet("QPushButton { background: rgba(255,255,255,0.05); color: #FFFFFF; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; } QPushButton:hover { border: 1px solid #00BFFF; }")
        cancel.clicked.connect(box.reject)
        hide.clicked.connect(box.accept)
        row.addWidget(cancel)
        row.addWidget(hide)
        flay.addLayout(row)
        box.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        box.move(screen.center().x() - box.width() // 2, screen.center().y() - box.height() // 2)
        if box.exec():
            self._invoke(self._on_hide_icon)

    def set_startup_workspace(self, enabled: bool):
        self._startup_btn.setChecked(bool(enabled))


class SmallPanelCard(QFrame):
    def __init__(self, title: str, body: str, *, accent: str = C.WHITE, parent=None):
        super().__init__(parent)
        self.setObjectName("SmallPanelCard")
        self.setStyleSheet(
            """
            QFrame#SmallPanelCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 18, 24, 230),
                    stop:1 rgba(9, 11, 15, 210));
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 14px;
            }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        t = QLabel(title.upper())
        t.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(t)

        self._body_lbl = QLabel(body)
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._body_lbl.setStyleSheet(f"color: {accent}; background: transparent;")
        lay.addWidget(self._body_lbl)

class BrahmaTelemetryWing(QFrame):
    """
    Brahma Right Wing: Live Operations, Research Streams, and Sources.
    Auto-dismisses in 10 seconds unless pinned or hovered.
    Adapts dynamically to the active theme color (Amber Gold by default).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BrahmaTelemetryWing")
        self.setFixedWidth(310)
        self.setMinimumHeight(320)
        self.setMaximumHeight(520)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.MinimumExpanding)
        self._pinned = False
        self._paused = False
        self._total_duration_ms = 10000
        self._remaining_ms = 10000
        self._sources_list = []
        self._theme_pri = "#00e5ff"
        self._theme_rgb = (0, 229, 255)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        # Header Row: [⚡ OPERATIONS] + Tool Tag + Pin + Close
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self._hud_tag = QLabel("⚡ [OPERATIONS]")
        self._hud_tag.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        header_row.addWidget(self._hud_tag)

        self._tool_tag = QLabel("TELEMETRY")
        self._tool_tag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        header_row.addWidget(self._tool_tag)
        header_row.addStretch()

        self._pin_btn = QPushButton("📌")
        self._pin_btn.setFixedSize(26, 26)
        self._pin_btn.setToolTip("Pin card (prevent auto-dismiss)")
        self._pin_btn.clicked.connect(self.toggle_pin)
        header_row.addWidget(self._pin_btn)

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.setToolTip("Dismiss")
        self._close_btn.clicked.connect(self.dismiss)
        header_row.addWidget(self._close_btn)
        lay.addLayout(header_row)

        # Operation Title
        self._title_lbl = QLabel("LIVE TASK EXECUTION")
        self._title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet("color: #ffffff;")
        self._title_lbl.setWordWrap(True)
        lay.addWidget(self._title_lbl)

        # Active Step Box
        self._step_box = QFrame()
        step_lay = QVBoxLayout(self._step_box)
        step_lay.setContentsMargins(12, 9, 12, 9)
        step_lay.setSpacing(4)

        step_hdr = QHBoxLayout()
        self._pulse_dot = QLabel("◉")
        self._pulse_dot.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        step_hdr.addWidget(self._pulse_dot)
        self._step_status_lbl = QLabel("ACTIVE STEP")
        self._step_status_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        step_hdr.addWidget(self._step_status_lbl)
        step_hdr.addStretch()
        step_lay.addLayout(step_hdr)

        self._step_text_lbl = QLabel("Initializing operation...")
        self._step_text_lbl.setFont(QFont("Segoe UI", 9))
        self._step_text_lbl.setStyleSheet("color: #E2E8F0;")
        self._step_text_lbl.setWordWrap(True)
        step_lay.addWidget(self._step_text_lbl)
        lay.addWidget(self._step_box)

        # Sources / References List
        self._sources_hdr = QLabel("LIVE SOURCES & CHANNELS")
        self._sources_hdr.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lay.addWidget(self._sources_hdr)

        self._sources_container = QWidget()
        self._sources_container.setStyleSheet("background: transparent;")
        self._sources_lay = QVBoxLayout(self._sources_container)
        self._sources_lay.setContentsMargins(0, 0, 0, 0)
        self._sources_lay.setSpacing(5)
        lay.addWidget(self._sources_container)

        lay.addStretch()

        # Bottom 10-Second Fuse
        fuse_row = QHBoxLayout()
        self._fuse_lbl = QLabel("10s")
        self._fuse_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        fuse_row.addWidget(self._fuse_lbl)
        self._fuse_bar = QProgressBar()
        self._fuse_bar.setRange(0, 100)
        self._fuse_bar.setValue(100)
        self._fuse_bar.setTextVisible(False)
        self._fuse_bar.setFixedHeight(4)
        fuse_row.addWidget(self._fuse_bar, stretch=1)
        lay.addLayout(fuse_row)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_tick)

        self._opacity_fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_fx)
        self._anim = QPropertyAnimation(self._opacity_fx, b"opacity", self)

        self.apply_theme()
        if hasattr(C, "register_listener"):
            C.register_listener(self.apply_theme)

        self.hide()

    def apply_theme(self):
        pri = getattr(C, "PRI", "#00e5ff") or "#00e5ff"
        try:
            r = int(pri[1:3], 16)
            g = int(pri[3:5], 16)
            b = int(pri[5:7], 16)
        except Exception:
            pri = "#00e5ff"
            r, g, b = 0, 229, 255

        self._theme_pri = pri
        self._theme_rgb = (r, g, b)

        self.setStyleSheet(f"""
            QFrame#BrahmaTelemetryWing {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(10, 15, 24, 238),
                    stop:0.6 rgba(6, 10, 18, 222),
                    stop:1 rgba(3, 5, 12, 212));
                border: 1px solid rgba({r}, {g}, {b}, 0.45);
                border-radius: 18px;
            }}
            QLabel {{
                background: transparent;
            }}
            QPushButton {{
                background: rgba({r}, {g}, {b}, 0.10);
                border: 1px solid rgba({r}, {g}, {b}, 0.28);
                border-radius: 6px;
                color: #ffffff;
                font-family: 'Segoe UI';
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background: rgba({r}, {g}, {b}, 0.26);
                border: 1px solid {pri};
                color: {pri};
            }}
        """)

        self._hud_tag.setStyleSheet(f"color: {pri}; letter-spacing: 1px; font-weight: bold;")
        self._tool_tag.setStyleSheet(f"""
            background: rgba({r}, {g}, {b}, 0.14);
            color: {pri};
            border: 1px solid rgba({r}, {g}, {b}, 0.38);
            border-radius: 9px;
            padding: 1px 7px;
            font-weight: bold;
        """)
        self._step_box.setStyleSheet(f"""
            background: rgba({r}, {g}, {b}, 0.07);
            border: 1px solid rgba({r}, {g}, {b}, 0.22);
            border-radius: 10px;
        """)
        self._pulse_dot.setStyleSheet(f"color: {pri}; font-weight: bold;")
        self._step_status_lbl.setStyleSheet(f"color: rgba({r}, {g}, {b}, 0.85); font-weight: bold;")
        self._sources_hdr.setStyleSheet("color: rgba(255, 255, 255, 0.60); letter-spacing: 0.5px; font-weight: bold;")
        self._fuse_lbl.setStyleSheet(f"color: rgba({r}, {g}, {b}, 0.75); font-weight: bold;")
        self._fuse_bar.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {pri};
                border-radius: 2px;
            }}
        """)
        if self._pinned:
            self._pin_btn.setStyleSheet(f"background: rgba({r}, {g}, {b}, 0.35); border: 1px solid {pri}; color: {pri};")
        else:
            self._pin_btn.setStyleSheet("")

    def enterEvent(self, event):
        super().enterEvent(event)
        self._paused = True

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._paused = False

    def toggle_pin(self):
        self._pinned = not self._pinned
        r, g, b = self._theme_rgb
        pri = self._theme_pri
        self._pin_btn.setStyleSheet(f"background: rgba({r}, {g}, {b}, 0.35); border: 1px solid {pri}; color: {pri};" if self._pinned else "")
        if self._pinned:
            self._fuse_lbl.setText("PINNED")
            self._fuse_bar.setValue(100)
        else:
            self._fuse_lbl.setText("10s")

    def show_operation(self, title: str, step: str, sources: list = None, tool: str = None):
        self.apply_theme()
        r, g, b = self._theme_rgb
        self._title_lbl.setText(title or "LIVE TASK EXECUTION")
        self._step_text_lbl.setText(step or "Running operation...")
        if tool:
            self._tool_tag.setText(str(tool).upper()[:14])

        # Clear and repopulate sources
        while self._sources_lay.count():
            item = self._sources_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sources = sources or []
        if not sources:
            default_item = QLabel("• System core processing data")
            default_item.setFont(QFont("Segoe UI", 8))
            default_item.setStyleSheet("color: rgba(255, 255, 255, 0.6);")
            self._sources_lay.addWidget(default_item)
        else:
            for s in sources[:4]:
                s_str = str(s).strip()
                if s_str.startswith("http"):
                    import urllib.parse
                    domain = urllib.parse.urlparse(s_str).netloc or s_str[:25]
                    text = f"🌐 {domain}"
                else:
                    text = f"📄 {s_str[:32]}"
                pill = QLabel(text)
                pill.setFont(QFont("Segoe UI", 8))
                pill.setStyleSheet(f"""
                    background: rgba(255, 255, 255, 0.05);
                    color: #E2E8F0;
                    border: 1px solid rgba({r}, {g}, {b}, 0.25);
                    border-radius: 6px;
                    padding: 3px 7px;
                """)
                self._sources_lay.addWidget(pill)

        self._remaining_ms = self._total_duration_ms
        self._paused = False
        if not self._pinned:
            self._timer.start()

        # Fade-in animation
        was_visible = self.isVisible()
        self.show()
        if not was_visible:
            sound_mgr.play_deploy_whoosh()
        else:
            sound_mgr.play_telemetry_chirp()
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.setDuration(250)
        self._anim.setStartValue(self._opacity_fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def _on_tick(self):
        if self._pinned or self._paused:
            return
        self._remaining_ms -= 100
        pct = max(0, int((self._remaining_ms / self._total_duration_ms) * 100))
        self._fuse_bar.setValue(pct)
        sec_left = max(0, int(math.ceil(self._remaining_ms / 1000.0)))
        self._fuse_lbl.setText(f"{sec_left}s")
        if self._remaining_ms <= 0:
            self._timer.stop()
            self.dismiss()

    def set_body(self, text: str):
        if hasattr(self, "_step_lbl") and text:
            self._step_lbl.setText(str(text))
            sound_mgr.play_telemetry_chirp()

    def dismiss(self):
        self._timer.stop()
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.setDuration(250)
        self._anim.setStartValue(self._opacity_fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.finished.connect(self.hide)
        self._anim.start()


class BrahmaResultWing(QFrame):
    """
    Brahma Left Wing: Final Results, Generated Deliverables (PDF/Word/Media/Code),
    Executive Summary Bullets, and Quick Action Buttons.
    Auto-dismisses in 10 seconds unless pinned or hovered.
    Adapts dynamically to the active theme color (Amber Gold by default).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BrahmaResultWing")
        self.setFixedWidth(310)
        self.setMinimumHeight(320)
        self.setMaximumHeight(540)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.MinimumExpanding)
        self._pinned = False
        self._paused = False
        self._total_duration_ms = 10000
        self._remaining_ms = 10000
        self._active_file_path = None
        self._theme_pri = "#00e5ff"
        self._theme_rgb = (0, 229, 255)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        # Header: [✨ DELIVERABLE] + Status Badge + Pin + Close
        header_row = QHBoxLayout()
        header_row.setSpacing(6)

        self._hud_tag = QLabel("✨ [DELIVERABLE]")
        self._hud_tag.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        header_row.addWidget(self._hud_tag)

        self._status_badge = QLabel("COMPLETED")
        self._status_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        header_row.addWidget(self._status_badge)
        header_row.addStretch()

        self._pin_btn = QPushButton("📌")
        self._pin_btn.setFixedSize(26, 26)
        self._pin_btn.setToolTip("Pin card (prevent auto-dismiss)")
        self._pin_btn.clicked.connect(self.toggle_pin)
        header_row.addWidget(self._pin_btn)

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.setToolTip("Dismiss")
        self._close_btn.clicked.connect(self.dismiss)
        header_row.addWidget(self._close_btn)
        lay.addLayout(header_row)

        # Title Label
        self._title_lbl = QLabel("MISSION RESULT")
        self._title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet("color: #ffffff;")
        self._title_lbl.setWordWrap(True)
        lay.addWidget(self._title_lbl)

        # File Card Section (Visible when deliverable is a file)
        self._file_card = QFrame()
        file_lay = QHBoxLayout(self._file_card)
        file_lay.setContentsMargins(12, 8, 12, 8)
        file_lay.setSpacing(10)

        self._file_icon_lbl = QLabel("📄")
        self._file_icon_lbl.setFont(QFont("Segoe UI", 16))
        file_lay.addWidget(self._file_icon_lbl)

        file_info_lay = QVBoxLayout()
        file_info_lay.setSpacing(2)
        self._file_name_lbl = QLabel("document.pdf")
        self._file_name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._file_name_lbl.setStyleSheet("color: #ffffff;")
        self._file_name_lbl.setWordWrap(True)
        file_info_lay.addWidget(self._file_name_lbl)

        self._file_size_lbl = QLabel("Ready to open")
        self._file_size_lbl.setFont(QFont("Segoe UI", 8))
        self._file_size_lbl.setStyleSheet("color: rgba(255, 255, 255, 0.6);")
        file_info_lay.addWidget(self._file_size_lbl)
        file_lay.addLayout(file_info_lay, stretch=1)

        self._btn_open_file = QPushButton("Open")
        self._btn_open_file.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_file.clicked.connect(self._on_open_file_clicked)
        file_lay.addWidget(self._btn_open_file)

        lay.addWidget(self._file_card)
        self._file_card.hide()

        # Summary / Bullets Scroll Area
        self._bullets_scroll = QScrollArea()
        self._bullets_scroll.setWidgetResizable(True)
        self._bullets_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._bullets_scroll.setStyleSheet("background: transparent; border: none;")
        self._bullets_container = QWidget()
        self._bullets_container.setStyleSheet("background: transparent;")
        self._bullets_lay = QVBoxLayout(self._bullets_container)
        self._bullets_lay.setContentsMargins(0, 0, 4, 0)
        self._bullets_lay.setSpacing(5)
        self._bullets_scroll.setWidget(self._bullets_container)
        lay.addWidget(self._bullets_scroll, stretch=1)

        # Actions Row: [Reveal in Folder] [Copy Result]
        self._actions_row = QHBoxLayout()
        self._actions_row.setSpacing(8)

        self._btn_explorer = QPushButton("📁 Reveal in Folder")
        self._btn_explorer.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_explorer.clicked.connect(self._on_reveal_clicked)
        self._actions_row.addWidget(self._btn_explorer)

        self._btn_copy = QPushButton("📋 Copy Result")
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.clicked.connect(self._on_copy_clicked)
        self._actions_row.addWidget(self._btn_copy)
        lay.addLayout(self._actions_row)

        # Bottom 10-Second Fuse
        fuse_row = QHBoxLayout()
        self._fuse_lbl = QLabel("10s")
        self._fuse_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        fuse_row.addWidget(self._fuse_lbl)
        self._fuse_bar = QProgressBar()
        self._fuse_bar.setRange(0, 100)
        self._fuse_bar.setValue(100)
        self._fuse_bar.setTextVisible(False)
        self._fuse_bar.setFixedHeight(4)
        fuse_row.addWidget(self._fuse_bar, stretch=1)
        lay.addLayout(fuse_row)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._on_tick)

        self._opacity_fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_fx)
        self._anim = QPropertyAnimation(self._opacity_fx, b"opacity", self)
        self._raw_result_text = ""

        self.apply_theme()
        if hasattr(C, "register_listener"):
            C.register_listener(self.apply_theme)

        self.hide()

    def apply_theme(self):
        pri = getattr(C, "PRI", "#00e5ff") or "#00e5ff"
        try:
            r = int(pri[1:3], 16)
            g = int(pri[3:5], 16)
            b = int(pri[5:7], 16)
        except Exception:
            pri = "#00e5ff"
            r, g, b = 0, 229, 255

        self._theme_pri = pri
        self._theme_rgb = (r, g, b)

        self.setStyleSheet(f"""
            QFrame#BrahmaResultWing {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(14, 18, 26, 240),
                    stop:0.6 rgba(9, 13, 20, 225),
                    stop:1 rgba(4, 7, 12, 215));
                border: 1px solid rgba({r}, {g}, {b}, 0.45);
                border-radius: 18px;
            }}
            QLabel {{
                background: transparent;
            }}
            QPushButton {{
                background: rgba({r}, {g}, {b}, 0.10);
                border: 1px solid rgba({r}, {g}, {b}, 0.28);
                border-radius: 6px;
                color: #ffffff;
                font-family: 'Segoe UI';
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background: rgba({r}, {g}, {b}, 0.26);
                border: 1px solid {pri};
                color: {pri};
            }}
        """)

        self._hud_tag.setStyleSheet(f"color: {pri}; letter-spacing: 1px; font-weight: bold;")
        self._status_badge.setStyleSheet(f"""
            background: rgba({r}, {g}, {b}, 0.15);
            color: {pri};
            border: 1px solid rgba({r}, {g}, {b}, 0.40);
            border-radius: 9px;
            padding: 1px 7px;
            font-weight: bold;
        """)
        self._file_card.setStyleSheet(f"""
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba({r}, {g}, {b}, 0.25);
            border-radius: 10px;
        """)
        self._fuse_lbl.setStyleSheet(f"color: rgba({r}, {g}, {b}, 0.75); font-weight: bold;")
        self._fuse_bar.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255, 255, 255, 0.08);
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {pri};
                border-radius: 2px;
            }}
        """)
        if self._pinned:
            self._pin_btn.setStyleSheet(f"background: rgba({r}, {g}, {b}, 0.35); border: 1px solid {pri}; color: {pri};")
        else:
            self._pin_btn.setStyleSheet("")

    def enterEvent(self, event):
        super().enterEvent(event)
        self._paused = True

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._paused = False

    def toggle_pin(self):
        self._pinned = not self._pinned
        r, g, b = self._theme_rgb
        pri = self._theme_pri
        self._pin_btn.setStyleSheet(f"background: rgba({r}, {g}, {b}, 0.35); border: 1px solid {pri}; color: {pri};" if self._pinned else "")
        if self._pinned:
            self._fuse_lbl.setText("PINNED")
            self._fuse_bar.setValue(100)
        else:
            self._fuse_lbl.setText("10s")

    def show_deliverable(self, title: str, summary: str = "", bullets: list = None,
                         file_path: str = None, kind: str = "result", actions: list = None, data: dict = None):
        self.apply_theme()
        self._title_lbl.setText(title or "MISSION RESULT")
        self._active_file_path = file_path
        self._raw_result_text = summary or "\n".join(bullets or [])

        # File card handling
        if file_path and Path(file_path).exists():
            p = Path(file_path)
            self._file_name_lbl.setText(p.name)
            suf = p.suffix.lower()
            icon_map = {
                ".pdf": ("📄", "PDF Document"),
                ".docx": ("📝", "Word Document"),
                ".xlsx": ("📊", "Spreadsheet"),
                ".pptx": ("📈", "Slide Presentation"),
                ".mp4": ("🎬", "Video File"),
                ".py": ("💻", "Python Script"),
                ".png": ("🖼️", "Image"),
                ".jpg": ("🖼️", "Image"),
            }
            icon, ftype = icon_map.get(suf, ("📁", "File"))
            self._file_icon_lbl.setText(icon)
            try:
                sz_mb = p.stat().st_size / (1024 * 1024)
                if sz_mb >= 0.1:
                    self._file_size_lbl.setText(f"{ftype} • {sz_mb:.1f} MB")
                else:
                    sz_kb = p.stat().st_size / 1024
                    self._file_size_lbl.setText(f"{ftype} • {sz_kb:.0f} KB")
            except Exception:
                self._file_size_lbl.setText(ftype)
            self._file_card.show()
            self._btn_explorer.show()
        else:
            self._file_card.hide()
            self._btn_explorer.hide()

        # Clear and repopulate bullets
        while self._bullets_lay.count():
            item = self._bullets_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        bullets = bullets or []
        if not bullets and summary:
            for sent in summary.split(". "):
                clean_s = sent.strip()
                if clean_s and len(clean_s) > 10:
                    bullets.append(clean_s + ("." if not clean_s.endswith(".") else ""))

        for b in bullets[:6]:
            b_lbl = QLabel(f"• {b}")
            b_lbl.setFont(QFont("Segoe UI", 9))
            b_lbl.setStyleSheet("color: #E2E8F0;")
            b_lbl.setWordWrap(True)
            self._bullets_lay.addWidget(b_lbl)

        self._remaining_ms = self._total_duration_ms
        self._paused = False
        if not self._pinned:
            self._timer.start()

        # Fade-in animation
        self.show()
        if kind == "deliverable" or file_path:
            sound_mgr.play_mission_complete()
        else:
            sound_mgr.play_deploy_whoosh()
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.setDuration(250)
        self._anim.setStartValue(self._opacity_fx.opacity())
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.start()

    def _on_tick(self):
        if self._pinned or self._paused:
            return
        self._remaining_ms -= 100
        pct = max(0, int((self._remaining_ms / self._total_duration_ms) * 100))
        self._fuse_bar.setValue(pct)
        sec_left = max(0, int(math.ceil(self._remaining_ms / 1000.0)))
        self._fuse_lbl.setText(f"{sec_left}s")
        if self._remaining_ms <= 0:
            self._timer.stop()
            self.dismiss()

    def dismiss(self):
        self._timer.stop()
        self._anim.stop()
        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.setDuration(250)
        self._anim.setStartValue(self._opacity_fx.opacity())
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.finished.connect(self.hide)
        self._anim.start()

    def _on_open_file_clicked(self):
        if self._active_file_path:
            p = Path(self._active_file_path).resolve()
            if p.exists():
                try:
                    if sys.platform == "win32":
                        os.startfile(str(p))
                    else:
                        import subprocess
                        subprocess.Popen(["xdg-open", str(p)])
                except Exception as e:
                    print(f"[BrahmaResultWing] Open file error: {e}")

    def _on_reveal_clicked(self):
        if self._active_file_path:
            p = Path(self._active_file_path).resolve()
            try:
                import subprocess
                if sys.platform == "win32":
                    if p.exists():
                        subprocess.Popen(["explorer.exe", f"/select,{str(p)}"])
                    elif p.parent.exists():
                        subprocess.Popen(["explorer.exe", str(p.parent)])
                else:
                    subprocess.Popen(["xdg-open", str(p.parent)])
            except Exception as e:
                print(f"[BrahmaResultWing] Reveal error: {e}")

    def set_body(self, text: str):
        if hasattr(self, "_summary_lbl") and text:
            self._summary_lbl.setText(str(text))

    def _on_copy_clicked(self):
        if self._raw_result_text:
            try:
                import pyperclip
                pyperclip.copy(self._raw_result_text)
                self._btn_copy.setText("✓ Copied!")
                QTimer.singleShot(1500, lambda: self._btn_copy.setText("📋 Copy Result"))
            except Exception:
                pass


class StatCard(QFrame):
    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setStyleSheet(
            """
            QFrame#StatCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(18, 20, 26, 240),
                    stop:1 rgba(8, 10, 14, 220));
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 16px;
            }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        lbl = QLabel(label.upper())
        lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        val = QLabel(value)
        val.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        val.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._detail_lbl = QLabel("")
        self._detail_lbl.setFont(QFont("Segoe UI", 7))
        self._detail_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(5)
        self._bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: rgba(255,255,255,0.05);
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {C.WHITE};
                border-radius: 2px;
            }}
            """
        )
        lay.addWidget(lbl)
        lay.addWidget(val)
        lay.addWidget(self._detail_lbl)
        lay.addWidget(self._bar)
        self._value_lbl = val

    def set_value(self, value: str, level: int | None = None, detail: str | None = None):
        self._value_lbl.setText(value)
        if detail is not None:
            self._detail_lbl.setText(detail)
        if level is not None:
            self._bar.setValue(max(0, min(100, int(level))))


class LogWidget(QScrollArea):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            f"""
            QScrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                border: none;
                margin: 6px 0 6px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 24px;
            }}
            """
        )
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self.setWidget(self._content)

        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        role, name, body = self._parse(text)
        stamp = time.strftime("%H:%M")

        card = MessageCard(role, name, body, stamp)
        self._layout.insertWidget(self._layout.count() - 1, card)
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _parse(self, text: str) -> tuple[str, str, str]:
        raw = (text or "").strip()
        tl = raw.lower()
        if tl.startswith("you:"):
            return "user", "You", raw[4:].strip()
        if tl.startswith("brahma evo:"):
            return "assistant", "Brahma Evo", raw[len("Brahma Evo:"):].strip()
        if tl.startswith("brahma evo:"):
            return "assistant", "Brahma Evo", raw[len("Brahma Evo:"):].strip()
        if tl.startswith("file:"):
            return "file", "File", raw[5:].strip()
        if tl.startswith("err:"):
            return "error", "System", raw[4:].strip()
        if tl.startswith("sys:"):
            return "system", "System", raw[4:].strip()
        return "system", "System", raw
