"""MainWindow's _PanelsMixin methods, moved verbatim out of ui.py (P4-19,
mixin split). Method bodies are byte-identical; module-level helpers and
config paths are injected via bind()."""
from __future__ import annotations

from core.ui_devices import BrahmaConnectDevicesPage
from core.ui_connectivity import SettingsHubPage
from core.ui_devices import SmartDevicesSection
from core.ui_connectivity import SystemConnectivityPage
from core.ui_connectivity import SystemConnectivitySidebar

from PyQt6.QtCore import (QSize, Qt)
from PyQt6.QtGui import (QColor, QFont, QIcon)
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)
from core.ui_theme import (C)
from smart_home_page_new import (BrahmaHomePage)


def bind(*, BrahmaResultWing, BrahmaTelemetryWing, InlineChatWorkspace, _RIGHT_W, _icon_pixmap):
    globals().update(BrahmaResultWing=BrahmaResultWing, BrahmaTelemetryWing=BrahmaTelemetryWing, InlineChatWorkspace=InlineChatWorkspace, _RIGHT_W=_RIGHT_W, _icon_pixmap=_icon_pixmap)


class _PanelsMixin:
    def _build_center_panel_modern(self, face_path: str) -> QWidget:
        w = QWidget()
        w.setObjectName("CenterStage")
        w.setStyleSheet("QWidget#CenterStage { background: transparent; border-left: none; border-right: none; }")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 0)
        lay.setSpacing(14)

        stage_frame = QFrame()
        stage_frame.setObjectName("StageFrame")
        stage_frame.setStyleSheet("QFrame#StageFrame { background: transparent; border: none; }")
        stage = QVBoxLayout(stage_frame)
        stage.setContentsMargins(26, 22, 26, 0)
        stage.setSpacing(14)
        lay.addWidget(stage_frame, stretch=1)

        # Internal labels kept as hidden placeholders for safety
        self._hidden_legacy_container = QWidget(self)
        self._hidden_legacy_container.setObjectName("HiddenLegacyContainer")
        self._hidden_legacy_container.hide()

        self._core_lbl = QLabel(self._hidden_legacy_container)
        self._core_sub_lbl = QLabel(self._hidden_legacy_container)
        self._core_status_lbl = QLabel(self._hidden_legacy_container)
        self._clock_lbl = QLabel(self._hidden_legacy_container)
        self._date_lbl = QLabel(self._hidden_legacy_container)
        self._time_status_lbl = QLabel(self._hidden_legacy_container)
        self._cpu_lbl = QLabel(self._hidden_legacy_container)
        self._ram_lbl = QLabel(self._hidden_legacy_container)
        self._status_chip = QLabel(self._hidden_legacy_container)
        self._smart_devices_section = SmartDevicesSection(self._hidden_legacy_container)
        self._smart_devices_section.hide()
        self._briefing_card = QFrame(self._hidden_legacy_container)
        self._briefing_card.hide()
        self._briefing_text_lbl = QLabel(self._hidden_legacy_container)
        self._developer_card = QFrame(self._hidden_legacy_container)
        self._developer_card.hide()
        self._developer_status_lbl = QLabel(self._hidden_legacy_container)

        self._hud_result_wing = BrahmaResultWing(self)
        self._hud_telemetry_wing = BrahmaTelemetryWing(self)
        self._command_card = self._hud_result_wing
        self._result_card = self._hud_telemetry_wing

        class _HudShim:
            def __init__(self):
                self.state = "ONLINE"
                self.speaking = False
                self.listening = False
                self.muted = False
            def set_state(self, s): self.state = s
            def set_audio_level(self, lvl): pass
            def feed_level(self, lvl): pass
            def update(self): pass
            def repaint(self): pass
            def show(self): pass
            def hide(self): pass
            def isVisible(self): return False
            def setVisible(self, v): pass
            def __getattr__(self, name):
                return lambda *args, **kwargs: None
        self.hud = _HudShim()

        command_row = QHBoxLayout()
        command_row.setContentsMargins(6, 0, 6, 0)
        command_row.setSpacing(12)
        command_row.addWidget(self._hud_result_wing, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        command_row.addStretch(1)
        command_row.addWidget(self._hud_telemetry_wing, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        stage.addLayout(command_row, stretch=1)

        self._command_panel = QWidget()
        self._command_panel.setStyleSheet("background: transparent;")
        cmd_lay = QVBoxLayout(self._command_panel)
        cmd_lay.setContentsMargins(0, 0, 0, 16)
        cmd_lay.setSpacing(10)
        cmd_lay.addLayout(self._build_command_row())
        stage.addWidget(self._command_panel)

        self._home_page = BrahmaHomePage()
        self._devices_page = BrahmaConnectDevicesPage(self)
        self._center_stack = QStackedWidget()
        self._center_stack.setStyleSheet("background: transparent; border: none;")
        self._center_stack.addWidget(w)
        self._center_stack.addWidget(self._home_page)
        self._center_stack.addWidget(self._devices_page)
        self._settings_page = SystemConnectivityPage()
        self._center_stack.addWidget(self._settings_page)
        
        self._settings_hub_page = SettingsHubPage(lambda idx: self._center_stack.setCurrentIndex(idx))
        self._center_stack.addWidget(self._settings_hub_page)

        self._center_stack.setCurrentIndex(0)
        return self._center_stack

    def _build_right_panel_modern(self) -> QWidget:
        w = QWidget()
        self._right_panel = w
        w.setObjectName("ModularRightSidebar")
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet(
            "QWidget#ModularRightSidebar { "
            "  background: rgba(8, 10, 15, 0.22); "
            "  border-left: 1px solid rgba(0, 229, 255, 0.25); "
            "}"
        )
        root_lay = QVBoxLayout(w)
        root_lay.setContentsMargins(14, 12, 14, 12)
        root_lay.setSpacing(10)

        # Modular Header
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(2, 2, 2, 2)
        header_bar.setSpacing(8)

        pulse_dot = QLabel("●")
        pulse_dot.setFont(QFont("Segoe UI", 10))
        pulse_dot.setStyleSheet("color: #37ff5f; background: transparent;")
        header_bar.addWidget(pulse_dot)

        header_title = QLabel("BRAHMA CHAT")
        header_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        header_title.setStyleSheet(f"color: {C.WHITE}; background: transparent; letter-spacing: 1px;")
        header_bar.addWidget(header_title)

        header_bar.addStretch(1)

        self._new_chat_btn = QPushButton("+ New")
        self._new_chat_btn.setFixedSize(54, 28)
        self._new_chat_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_chat_btn.setToolTip("Start a new chat session")
        self._new_chat_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0, 229, 255, 0.10); color: {C.PRI}; border: 1px solid rgba(0, 229, 255, 0.30); border-radius: 6px; padding: 2px 6px; }}"
            f"QPushButton:hover {{ background: rgba(0, 229, 255, 0.22); color: #FFFFFF; border-color: {C.PRI}; }}"
        )
        self._new_chat_btn.clicked.connect(lambda: self._inline_workspace.new_conversation() if hasattr(self, "_inline_workspace") else None)
        header_bar.addWidget(self._new_chat_btn)

        self._right_toggle_btn = QPushButton("❯")
        self._right_toggle_btn.setFixedSize(28, 28)
        self._right_toggle_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._right_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._right_toggle_btn.setToolTip("Collapse Chat Sidebar")
        self._right_toggle_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.06); color: {C.WHITE}; border: 1px solid rgba(255,255,255,0.12); border-radius: 6px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; background: rgba(0, 229, 255,0.12); }}"
        )
        self._right_toggle_btn.clicked.connect(self._toggle_right_sidebar)
        header_bar.addWidget(self._right_toggle_btn)
        root_lay.addLayout(header_bar)

        self._right_stack = QStackedWidget()
        self._right_stack.setStyleSheet("background: transparent; border: none;")

        self._right_content = QWidget()
        self._right_content.setStyleSheet("background: transparent;")
        chat_lay = QVBoxLayout(self._right_content)
        chat_lay.setContentsMargins(0, 4, 0, 0)
        chat_lay.setSpacing(8)

        self._inline_workspace = InlineChatWorkspace()
        self._inline_workspace.attach_requested.connect(self._browse_attachment)
        self._inline_workspace.mic_requested.connect(self._toggle_mute)
        self._inline_workspace.command_submitted.connect(self._send)
        self._log = self._inline_workspace
        chat_lay.addWidget(self._inline_workspace, stretch=1)

        self._settings_sidebar = SystemConnectivitySidebar()
        self._empty_right = QWidget()
        self._empty_right.setStyleSheet("background: transparent;")

        self._right_stack.addWidget(self._right_content)
        self._right_stack.addWidget(self._settings_sidebar)
        self._right_stack.addWidget(self._empty_right)
        root_lay.addWidget(self._right_stack, stretch=1)

        self._apply_sidebar_state()
        return w

    def _build_command_row(self) -> QHBoxLayout:
        wrapper = QHBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)

        bar = QFrame()
        bar.setFixedHeight(84)
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bar.setStyleSheet(
            """
            QFrame {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(0, 229, 255, 0.20);
                border-radius: 35px;
            }
            """
        )
        try:
            shadow = QGraphicsDropShadowEffect(bar)
            shadow.setBlurRadius(24)
            shadow.setColor(QColor(0, 229, 255, 45))
            shadow.setOffset(0, 0)
            bar.setGraphicsEffect(shadow)
        except Exception:
            pass
        row = QHBoxLayout(bar)
        row.setContentsMargins(18, 16, 18, 16)
        row.setSpacing(12)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask Brahma Evo anything...")
        self._input.setFont(QFont("Segoe UI", 10))
        self._input.setFixedHeight(50)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: transparent;
                color: {C.WHITE};
                border: none;
                padding: 0 14px;
                selection-background-color: rgba(0, 229, 255, 0.25);
            }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input, stretch=1)

        icon_button_style = f"""
            QPushButton {{
                background: transparent;
                color: {C.WHITE};
                border: none;
                border-radius: 22px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.15);
                color: {C.PRI};
            }}
            QPushButton:pressed {{
                background: rgba(0, 229, 255, 0.30);
            }}
        """

        attach = QPushButton()
        attach.setFixedSize(44, 44)
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setToolTip("Attach file")
        attach.setIcon(QIcon(_icon_pixmap("attach", 20)))
        attach.setIconSize(QSize(20, 20))
        attach.setStyleSheet(icon_button_style)
        attach.clicked.connect(self._browse_attachment)
        row.addWidget(attach)

        mic = QPushButton()
        mic.setFixedSize(44, 44)
        mic.setCursor(Qt.CursorShape.PointingHandCursor)
        mic.setToolTip("Microphone")
        mic.setIcon(QIcon(_icon_pixmap("mic", 20)))
        mic.setIconSize(QSize(20, 20))
        mic.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.10);
                color: {C.PRI};
                border: 1px solid rgba(0, 229, 255, 0.30);
                border-radius: 22px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.25);
                border: 1px solid rgba(0, 229, 255, 0.60);
            }}
            QPushButton:pressed {{
                background: rgba(0, 229, 255, 0.40);
            }}
        """)
        mic.clicked.connect(self._toggle_mute)
        row.addWidget(mic)

        self._send_btn = QPushButton()
        self._send_btn.setFixedSize(44, 44)
        self._send_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setIcon(QIcon(_icon_pixmap("send", 20)))
        self._send_btn.setIconSize(QSize(20, 20))
        self._send_btn.setStyleSheet(icon_button_style)
        self._send_btn.clicked.connect(self._send)
        row.addWidget(self._send_btn)

        wrapper.addWidget(bar)
        return wrapper
