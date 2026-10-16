from __future__ import annotations
from core.user_paths import get_user_data_dir, atomic_write_json

import asyncio
import json
import html as html_lib
import math
import os

# _quiet_run() spawns helper processes and the startup handler prints a
# traceback. Both names were used in this module without ever being imported,
# so every _quiet_run() caller raised NameError and the startup error handler
# masked the real exception with its own.
import subprocess
import traceback
from typing import Any

# Hardware acceleration & WebGL flags for smooth 180fps+ rendering in Chromium.
# Applied through the shared bootstrap (see core.qt_bootstrap) instead of a
# local copy so the flag string cannot drift from main.py/globe_window.py.
from core.qt_bootstrap import bootstrap
bootstrap()

import platform
import random
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

import psutil
if platform.system() == "Windows":
    import winreg

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt,
    QTimer, QUrl, QPropertyAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QBrush, QColor, QDragEnterEvent, QDropEvent, QFont,
    QIcon, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSlider, QTextEdit,
    QGraphicsDropShadowEffect,
    QStyle, QSystemTrayIcon, QVBoxLayout, QWidget, QProgressBar,
    QStackedWidget, QInputDialog, QMessageBox,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEB_ENGINE_AVAILABLE = True
except Exception:
    WEB_ENGINE_AVAILABLE = False

from discord_bot import DiscordBotService
from gesture_utils import estimate_gesture_state, GestureTracker
from smart_home import SmartHomeService
from smart_home_page_new import BrahmaHomePage, _DeviceTile
from core.local_brain import local_brain
from workspace_store import store as workspace_store
from core.identity import identity
from sound_manager import sound_mgr

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = get_user_data_dir() / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"
APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.json"
DISCORD_SETTINGS_FILE = CONFIG_DIR / "discord_bot.json"
LOGO_FILE  = BASE_DIR / "assets" / "Brahma_Lite_Logo.png"
LOGO_ICO   = BASE_DIR / "assets" / "Brahma_Lite_Logo.ico"
BACKGROUND_IMAGE_FILE = BASE_DIR / "assets" / "background.png"
MODEL_DOWNLOAD_URL = "https://storage.googleapis.com/mediapipe-assets/hand_landmarker.task"

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 820, 580
_LEFT_W  = 160
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


from core.ui_theme import C

try:
    if APP_SETTINGS_FILE.exists():
        with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
            _global_settings = json.load(f)
            C.load_theme(_global_settings.get("app_theme", "#ffffff"))
except Exception:
    pass

# Patch setStyleSheet to dynamically replace hardcoded gold colors with active white and ice theme
_old_setStyleSheet = QWidget.setStyleSheet
def _new_setStyleSheet(self, style):
    if style:
        style = style.replace("#00e5ff", getattr(C, "ACC", "#00e5ff"))
        style = style.replace("#00e5ff", getattr(C, "ACC", "#00e5ff"))
        style = style.replace("#00e5ff", getattr(C, "ACC", "#00e5ff"))
        style = style.replace("#ffd700", getattr(C, "ACC", "#00e5ff"))
        style = style.replace("#d4af37", getattr(C, "ACC", "#00e5ff"))
        style = style.replace("#80ffff", getattr(C, "ACC2", "#80ffff"))
        style = style.replace("#00b4d8", getattr(C, "ACC", "#00e5ff"))
        style = style.replace("255, 179, 0", "0, 229, 255")
        style = style.replace("255,179,0", "0,229,255")
        style = style.replace("255, 190, 26", "0, 229, 255")
        style = style.replace("244, 180, 0", "0, 229, 255")
    _old_setStyleSheet(self, style)
QWidget.setStyleSheet = _new_setStyleSheet


from core import ui_bg as _ui_bg

_ui_bg.bind(
    BASE_DIR=BASE_DIR,
)

BackgroundWidget = _ui_bg.BackgroundWidget


from core import ui_overlays as _ui_overlays

RemoteKeyOverlay = _ui_overlays.RemoteKeyOverlay
DailyBriefingOverlay = _ui_overlays.DailyBriefingOverlay
ConfirmationOverlay = _ui_overlays.ConfirmationOverlay
MemoryInspectorOverlay = _ui_overlays.MemoryInspectorOverlay


from core import ui_branding as _ui_branding

_ui_branding.bind(
    LOGO_FILE=LOGO_FILE,
    LOGO_ICO=LOGO_ICO,
)

qcol = _ui_branding.qcol
_logo_icon = _ui_branding._logo_icon
_logo_pixmap = _ui_branding._logo_pixmap
_framed_logo = _ui_branding._framed_logo
_icon_pixmap = _ui_branding._icon_pixmap
_attach_pulse_glow = _ui_branding._attach_pulse_glow


from core import ui_startup as _ui_startup

_ui_startup.bind(
    BASE_DIR=BASE_DIR,
    _OS=_OS,
)

_quiet_run = _ui_startup._quiet_run
_quote_cmd_arg = _ui_startup._quote_cmd_arg
_hidden_launch_args = _ui_startup._hidden_launch_args
_startup_run_value = _ui_startup._startup_run_value
_startup_registry_key = _ui_startup._startup_registry_key
_current_boot_stamp = _ui_startup._current_boot_stamp
_launched_from_windows_startup = _ui_startup._launched_from_windows_startup
_default_app_settings = _ui_startup._default_app_settings
_default_discord_settings = _ui_startup._default_discord_settings

from core import ui_sysmetrics as _ui_sysmetrics

_ui_sysmetrics.bind(
    _OS=_OS,
    _quiet_run=_quiet_run,
)

_SysMetrics = _ui_sysmetrics._SysMetrics


_metrics = _SysMetrics()

_CAM_OK_CACHE = {"ok": False, "ts": 0.0}



from core import ui_gesture as _ui_gesture

_ui_gesture.bind(
    CONFIG_DIR=CONFIG_DIR,
    MODEL_DOWNLOAD_URL=MODEL_DOWNLOAD_URL,
)

_camera_available = _ui_gesture._camera_available
_GestureRenderCanvas = _ui_gesture._GestureRenderCanvas
GestureCameraPreview = _ui_gesture.GestureCameraPreview


from core import ui_widgets as _ui_widgets

_active_net_label = _ui_widgets._active_net_label
MetricBar = _ui_widgets.MetricBar

from core import ui_chat as _ui_chat

_ui_chat.bind(C=C, _framed_logo=_framed_logo, BASE_DIR=BASE_DIR)

MessageCard = _ui_chat.MessageCard
TaskCard = _ui_chat.TaskCard
AttachmentCard = _ui_chat.AttachmentCard
EventCard = _ui_chat.EventCard
ArtifactCard = _ui_chat.ArtifactCard
ChatBubble = _ui_chat.ChatBubble
HistoryConversationItem = _ui_chat.HistoryConversationItem
ConversationFeed = _ui_chat.ConversationFeed
TaskDock = _ui_chat.TaskDock
WorkspaceSidebar = _ui_chat.WorkspaceSidebar
InlineChatWorkspace = _ui_chat.InlineChatWorkspace



from core import ui_telemetry as _ui_telemetry

_ui_telemetry.bind(
    MessageCard=MessageCard,
)

LauncherControlPanel = _ui_telemetry.LauncherControlPanel
SmallPanelCard = _ui_telemetry.SmallPanelCard
BrahmaTelemetryWing = _ui_telemetry.BrahmaTelemetryWing
BrahmaResultWing = _ui_telemetry.BrahmaResultWing
StatCard = _ui_telemetry.StatCard
LogWidget = _ui_telemetry.LogWidget

_FILE_ICONS = {
    "image":   ("ðŸ-¼", "#00d4ff"), "video":   ("ðŸŽ¬", "#ff6b00"),
    "audio":   ("ðŸŽµ", "#cc44ff"), "pdf":     ("ðŸ“„", "#ff4444"),
    "word":    ("ðŸ“", "#4488ff"), "excel":   ("ðŸ“Š", "#44bb44"),
    "code":    ("ðŸ’»", "#ffcc00"), "archive": ("ðŸ“¦", "#ff8844"),
    "pptx":    ("ðŸ“Š", "#ff6622"), "text":    ("ðŸ“ƒ", "#aaaaaa"),
    "data":    ("ðŸ”§", "#88ddff"), "unknown": ("ðŸ“Ž", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

from core import ui_filedrop as _ui_filedrop

_ui_filedrop.bind(
    _EXT_TO_CAT=_EXT_TO_CAT,
    _FILE_ICONS=_FILE_ICONS,
    _OS=_OS,
    qcol=qcol,
)

_file_category = _ui_filedrop._file_category
_fmt_size = _ui_filedrop._fmt_size
FileDropZone = _ui_filedrop.FileDropZone
_DropCanvas = _ui_filedrop._DropCanvas


from core import ui_setup as _ui_setup

_ui_setup.bind(
    APP_SETTINGS_FILE=APP_SETTINGS_FILE,
    _OS=_OS,
)

SetupOverlay = _ui_setup.SetupOverlay




from core import ui_commandbar as _ui_commandbar

_ui_commandbar.bind(
    _icon_pixmap=_icon_pixmap,
)

CommandBar = _ui_commandbar.CommandBar


from core import ui_devmode as _ui_devmode

_ui_devmode.bind(
    BASE_DIR=BASE_DIR,
)

DeveloperModeDialog = _ui_devmode.DeveloperModeDialog


from core import ui_transients as _ui_transients

_ease_out_expo = _ui_transients._ease_out_expo
_ease_in_out_sine = _ui_transients._ease_in_out_sine
ScanningOverlay = _ui_transients.ScanningOverlay
BootSequenceOverlay = _ui_transients.BootSequenceOverlay
IncomingAlertDialog = _ui_transients.IncomingAlertDialog
MeetingOverlay = _ui_transients.MeetingOverlay


from core import ui_floating as _ui_floating

FloatingLauncher = _ui_floating.FloatingLauncher
FloatingGestureCard = _ui_floating.FloatingGestureCard


from core import ui_discord as _ui_discord
_ui_discord.bind(
    CONFIG_DIR=CONFIG_DIR,
    DISCORD_SETTINGS_FILE=DISCORD_SETTINGS_FILE,
    _default_discord_settings=_default_discord_settings,
)
_DiscordMixin = _ui_discord._DiscordMixin
from core import ui_appsettings as _ui_appsettings
_ui_appsettings.bind(
    APP_SETTINGS_FILE=APP_SETTINGS_FILE,
    CONFIG_DIR=CONFIG_DIR,
    _current_boot_stamp=_current_boot_stamp,
    _default_app_settings=_default_app_settings,
)
_AppSettingsMixin = _ui_appsettings._AppSettingsMixin
from core import ui_startup_os as _ui_startup_os
_ui_startup_os.bind(
    API_FILE=API_FILE,
    _startup_registry_key=_startup_registry_key,
    _startup_run_value=_startup_run_value,
)
_StartupOSMixin = _ui_startup_os._StartupOSMixin
from core import ui_avatar_audio as _ui_avatar_audio
_AvatarAudioMixin = _ui_avatar_audio._AvatarAudioMixin
from core import ui_overlays_cards as _ui_overlays_cards
_ui_overlays_cards.bind(
    ConfirmationOverlay=ConfirmationOverlay,
    DailyBriefingOverlay=DailyBriefingOverlay,
)
_OverlayCardsMixin = _ui_overlays_cards._OverlayCardsMixin
from core import ui_mute_send as _ui_mute_send
_MuteSendMixin = _ui_mute_send._MuteSendMixin
from core import ui_meeting as _ui_meeting
_ui_meeting.bind(
    IncomingAlertDialog=IncomingAlertDialog,
)
_MeetingMixin = _ui_meeting._MeetingMixin
from core import ui_nav as _ui_nav
_ui_nav.bind(
    _LEFT_W=_LEFT_W,
    _RIGHT_W=_RIGHT_W,
)
_NavMixin = _ui_nav._NavMixin
from core import ui_remote as _ui_remote
_ui_remote.bind(
    RemoteKeyOverlay=RemoteKeyOverlay,
)
_RemoteMixin = _ui_remote._RemoteMixin
from core import ui_secondary_overlays as _ui_secondary_overlays
_ui_secondary_overlays.bind(
    MemoryInspectorOverlay=MemoryInspectorOverlay,
)
_SecondaryOverlaysMixin = _ui_secondary_overlays._SecondaryOverlaysMixin
from core import ui_state_appliers as _ui_state_appliers
_ui_state_appliers.bind(
    API_FILE=API_FILE,
    MeetingOverlay=MeetingOverlay,
    ScanningOverlay=ScanningOverlay,
)
_StateAppliersMixin = _ui_state_appliers._StateAppliersMixin
from core import ui_setup_flow as _ui_setup_flow
_ui_setup_flow.bind(
    API_FILE=API_FILE,
    CONFIG_DIR=CONFIG_DIR,
    SetupOverlay=SetupOverlay,
)
_SetupFlowMixin = _ui_setup_flow._SetupFlowMixin
from core import ui_panels as _ui_panels
_ui_panels.bind(
    BrahmaResultWing=BrahmaResultWing,
    BrahmaTelemetryWing=BrahmaTelemetryWing,
    InlineChatWorkspace=InlineChatWorkspace,
    _RIGHT_W=_RIGHT_W,
    _icon_pixmap=_icon_pixmap,
)
_PanelsMixin = _ui_panels._PanelsMixin
from core import ui_wiring as _ui_wiring
_WiringMixin = _ui_wiring._WiringMixin
from core import ui_runtime as _ui_runtime
_ui_runtime.bind(
    _active_net_label=_active_net_label,
    _camera_available=_camera_available,
    _metrics=_metrics,
)
_RuntimeMixin = _ui_runtime._RuntimeMixin
from core import ui_apply_state as _ui_apply_state
_ApplyStateMixin = _ui_apply_state._ApplyStateMixin
class MainWindow(_ApplyStateMixin, _RuntimeMixin, _WiringMixin, _PanelsMixin, _SetupFlowMixin, _StateAppliersMixin, _SecondaryOverlaysMixin, _RemoteMixin, _NavMixin, _MeetingMixin, _MuteSendMixin, _OverlayCardsMixin, _AvatarAudioMixin, _StartupOSMixin, _AppSettingsMixin, _DiscordMixin, QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _scan_sig  = pyqtSignal(bool, str)
    _briefing_sig = pyqtSignal(object)
    _briefing_hide_sig = pyqtSignal()
    _attention_sig = pyqtSignal(object)
    _meeting_sig = pyqtSignal(object)
    _task_workspace_sig = pyqtSignal(object)
    _screen_capture_sig = pyqtSignal()
    discord_config_changed = pyqtSignal(object)
    discord_status_changed = pyqtSignal(str)
    minimized = pyqtSignal()
    restored = pyqtSignal()
    _hud_operation_sig = pyqtSignal(dict)
    _hud_deliverable_sig = pyqtSignal(dict)
    _circuit_hud_sig = pyqtSignal(object)
    _call_screening_sig = pyqtSignal(object)
    _confirm_sig = pyqtSignal(str, str, str)
    _memory_overlay_sig = pyqtSignal(str)
    _audio_level_sig = pyqtSignal(float)
    live_mode_changed = pyqtSignal(str)
    live_call_toggled = pyqtSignal(bool)

    def _make_window_icon(self) -> QIcon:
        return _logo_icon()

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowFlag(Qt.WindowType.Tool, False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowIcon(self._make_window_icon())
        self.setWindowTitle("Brahma Evo")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_attention_action = None
        self.on_chat_event = None
        self.on_remote_clicked = None
        self._muted           = False
        self._wakeword_listening = False
        self._current_file: str | None = None
        self._state = "LISTENING"
        self._left_collapsed  = False
        self._right_collapsed = False
        self._current_page = "dashboard"
        self._settings_bridge = None
        self._api_ready = False
        self._app_settings_cache: dict | None = None
        self._overlay: QWidget | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._scan_overlay: ScanningOverlay | None = None
        self._incoming_alert: IncomingAlertDialog | None = None
        self._meeting_overlay: MeetingOverlay | None = None
        self._circuit_overlay: QWidget | None = None
        self._call_screening_dialog: QDialog | None = None
        self._call_screening_transcript: QLabel | None = None
        self._meeting_overlay_collapsed = False
        self._chat_source_queue: deque[str] = deque()

        central = BackgroundWidget(BACKGROUND_IMAGE_FILE if BACKGROUND_IMAGE_FILE.exists() else None)
        central.setStyleSheet("background: transparent;")
        self._bg_widget = central
        self.setCentralWidget(central)
        self._attach_avatar_renderer()

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._taskbar = QWidget()
        self._taskbar.setStyleSheet("background: transparent;")
        taskbar_lay = QHBoxLayout(self._taskbar)
        taskbar_lay.setContentsMargins(10, 10, 10, 10)
        
        self._btn_dashboard = QPushButton("Dashboard")
        self._btn_chat = QPushButton("Chat")
        self._btn_settings = QPushButton("Settings")

        for btn in (self._btn_dashboard, self._btn_chat, self._btn_settings):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"QPushButton {{ background: rgba(12,14,18,200); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 5px 15px; font-weight: bold; font-family: 'Segoe UI'; font-size: 13px; }} QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI}; }}")
        
        self._status_badge = QLabel("● ONLINE")
        self._status_badge.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._status_badge.setStyleSheet(
            f"color: {C.PRI}; background: rgba(12,14,18,220); border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 5px 12px;"
        )
        taskbar_lay.addWidget(self._status_badge)
        taskbar_lay.addStretch()
        taskbar_lay.addWidget(self._btn_dashboard)
        taskbar_lay.addWidget(self._btn_chat)
        taskbar_lay.addWidget(self._btn_settings)
        taskbar_lay.addStretch()
        
        root.addWidget(self._taskbar)

        self._floating_gesture_card = FloatingGestureCard(self.centralWidget())
        self._floating_gesture_card.show()

        self._center_panel = self._build_center_panel_modern(face_path)
        body.addWidget(self._center_panel, stretch=1)
        _attach_pulse_glow(self._center_panel, color=C.PRI, blur_min=6.0, blur_max=14.0, alpha=36, period_ms=4200)

        self._right_collapsed = False
        self._right_panel = self._build_right_panel_modern()
        body.addWidget(self._right_panel, stretch=0)

        self._btn_dashboard.clicked.connect(self._on_nav_dashboard)
        self._btn_chat.clicked.connect(self._toggle_right_sidebar)
        self._btn_settings.clicked.connect(self._on_nav_settings)
        self._update_nav_styles()

        root.addLayout(body, stretch=1)

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik gÃ¼ncelleme timer'Ä±
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._on_log_text)
        self._state_sig.connect(self._apply_state)
        self._briefing_sig.connect(self._apply_daily_briefing)
        self._briefing_hide_sig.connect(self._schedule_daily_briefing_hide)
        self._attention_sig.connect(self._show_attention_alert)
        self._meeting_sig.connect(self._apply_meeting_state)
        self._task_workspace_sig.connect(self._apply_task_workspace)
        self._scan_sig.connect(self._apply_scan_state)
        self._screen_capture_sig.connect(self._on_screen_capture_requested)
        self.discord_status_changed.connect(self._on_discord_status_update)
        self._hud_operation_sig.connect(self._apply_hud_operation)
        self._hud_deliverable_sig.connect(self._apply_hud_deliverable)
        self._circuit_hud_sig.connect(self._apply_circuit_overlay)
        self._call_screening_sig.connect(self._apply_call_screening)
        self._confirm_sig.connect(self._apply_confirm_overlay)
        self._memory_overlay_sig.connect(self._apply_memory_overlay)
        self._audio_level_sig.connect(self._on_audio_level_sig)
        try:
            from core import confirm as confirm_gate
            confirm_gate.bind(self.show_confirm, self.hide_confirm, self._log_sig.emit)
        except Exception:
            pass

        import threading
        self._screen_capture_event = threading.Event()

        self._ready = False
        self._card_hide_tmr = QTimer(self)
        self._card_hide_tmr.setSingleShot(True)
        self._card_hide_tmr.timeout.connect(self._hide_command_cards)
        self._briefing_hide_tmr = QTimer(self)
        self._briefing_hide_tmr.setSingleShot(True)
        self._briefing_hide_tmr.timeout.connect(self._hide_daily_briefing_card)

        self._ready = self._check_config()
        self._api_ready = self._ready
        if self._ready:
            self._apply_state("LISTENING")
        else:
            self._show_setup(self._load_api_defaults())
        self._scan_sig.connect(self._apply_scan_state)

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_left = QShortcut(QKeySequence("Ctrl+["), self)
        sc_left.activated.connect(self._toggle_left_sidebar)
        sc_right = QShortcut(QKeySequence("Ctrl+]"), self)
        sc_right.activated.connect(self._toggle_right_sidebar)


    def resizeEvent(self, event):
        if event:
            super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible() and self.centralWidget():
            cw = self.centralWidget()
            self._overlay.setGeometry(0, 0, cw.width(), cw.height())
        if hasattr(self, '_floating_gesture_card') and self.centralWidget():
            cw = self.centralWidget()
            rw = self._right_panel.width() if hasattr(self, '_right_panel') and self._right_panel.isVisible() and not getattr(self, '_right_collapsed', False) else 0
            self._floating_gesture_card.move(cw.width() - rw - self._floating_gesture_card.width() - 20, 20)
        if getattr(self, '_briefing_overlay', None) and self._briefing_overlay.isVisible():
            self._position_briefing_overlay()
        if getattr(self, '_remote_overlay', None) and self._remote_overlay.isVisible():
            self._position_remote_overlay()
        if getattr(self, '_confirm_overlay', None) and self._confirm_overlay.isVisible():
            self._position_confirm_overlay()
        if getattr(self, '_memory_overlay', None) and self._memory_overlay.isVisible():
            self._position_memory_overlay()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if hasattr(self, "_bg_widget") and hasattr(self._bg_widget, "set_pointer_norm"):
            w = max(1, self.width())
            h = max(1, self.height())
            pos = event.position() if hasattr(event, "position") else event.pos()
            nx = (pos.x() / w) * 2.0 - 1.0
            ny = -(pos.y() / h) * 2.0 + 1.0
            self._bg_widget.set_pointer_norm(nx, ny)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self.minimized.emit()
            else:
                self.restored.emit()

from core import ui_connectivity as _ui_connectivity

_ui_connectivity.bind(
    C=C,
    BASE_DIR=BASE_DIR,
    CONFIG_DIR=CONFIG_DIR,
    APP_SETTINGS_FILE=APP_SETTINGS_FILE,
    _default_app_settings=_default_app_settings,
    _default_discord_settings=_default_discord_settings,
)

SystemConnectivitySidebar = _ui_connectivity.SystemConnectivitySidebar
_ModelDiscoveryBridge = _ui_connectivity._ModelDiscoveryBridge
SettingsHubPage = _ui_connectivity.SettingsHubPage
SystemConnectivityPage = _ui_connectivity.SystemConnectivityPage

from core import ui_devices as _ui_devices

_ui_devices.bind(
    APP_SETTINGS_FILE=APP_SETTINGS_FILE,
    C=C,
)

SmartDevicesSection = _ui_devices.SmartDevicesSection
_ConnectDeviceCard = _ui_devices._ConnectDeviceCard
BrahmaConnectDevicesPage = _ui_devices.BrahmaConnectDevicesPage

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


from core import ui_shell_settings as _ui_shell_settings
_ui_shell_settings.bind(
    APP_SETTINGS_FILE=APP_SETTINGS_FILE,
    CONFIG_DIR=CONFIG_DIR,
    _default_app_settings=_default_app_settings,
)
_ShellSettingsMixin = _ui_shell_settings._ShellSettingsMixin
from core import ui_shell_discord as _ui_shell_discord
_ui_shell_discord.bind(
    CONFIG_DIR=CONFIG_DIR,
    DISCORD_SETTINGS_FILE=DISCORD_SETTINGS_FILE,
    _default_discord_settings=_default_discord_settings,
)
_ShellDiscordMixin = _ui_shell_discord._ShellDiscordMixin
from core import ui_shell_boot as _ui_shell_boot
_ui_shell_boot.bind(
    BootSequenceOverlay=BootSequenceOverlay,
    _current_boot_stamp=_current_boot_stamp,
)
_ShellBootMixin = _ui_shell_boot._ShellBootMixin
from core import ui_shell_tray as _ui_shell_tray
_ShellTrayMixin = _ui_shell_tray._ShellTrayMixin
from core import ui_shell_devmode as _ui_shell_devmode
_ui_shell_devmode.bind(
    DeveloperModeDialog=DeveloperModeDialog,
)
_ShellDevModeMixin = _ui_shell_devmode._ShellDevModeMixin
from core import ui_shell_sidebar as _ui_shell_sidebar
_ShellSidebarMixin = _ui_shell_sidebar._ShellSidebarMixin
from core import ui_shell_launcher as _ui_shell_launcher
_ui_shell_launcher.bind(
    LauncherControlPanel=LauncherControlPanel,
)
_ShellLauncherMixin = _ui_shell_launcher._ShellLauncherMixin
from core import ui_shell_controlpanel as _ui_shell_controlpanel
_ShellControlPanelMixin = _ui_shell_controlpanel._ShellControlPanelMixin
class BrahmaUI(_ShellControlPanelMixin, _ShellLauncherMixin, _ShellSidebarMixin, _ShellDevModeMixin, _ShellTrayMixin, _ShellBootMixin, _ShellDiscordMixin, _ShellSettingsMixin):
    def __init__(self, face_path: str, size=None, *, show_immediately: bool = True):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._app.setQuitOnLastWindowClosed(False)
        self._app.setApplicationDisplayName("Brahma Evo")
        self._app.setWindowIcon(self._make_app_icon())
        try:
            current_store = workspace_store()
            current_store.rollover_active_conversation_on_startup()
        except Exception:
            pass
        self._win = MainWindow(face_path)
        try:
            from core.updater import UpdateChecker
            self._updater = UpdateChecker()
            self._updater.update_available_sig.connect(self._show_update_prompt)
            self._updater.start()
        except Exception as e:
            print(f"Failed to start updater: {e}")
        try:
            self._win._startup_enabled()
        except Exception:
            pass
        self._win.set_settings_bridge(self)
        self._discord_service = DiscordBotService(
            status_callback=self._win.discord_status_changed.emit,
            log_callback=self._win._log_sig.emit,
        )
        self._discord_service.bind_app_submitter(self._win.submit_command)
        self._win.discord_config_changed.connect(self._on_discord_config_changed)
        self._win.on_chat_event = self._on_chat_event
        self._app.aboutToQuit.connect(self._discord_service.stop)
        self._launcher = FloatingLauncher()
        self._command_bar = CommandBar()
        self._workspace_sidebar = WorkspaceSidebar()
        self._control_panel: LauncherControlPanel | None = None
        self._boot_overlay: BootSequenceOverlay | None = None
        self._app_settings_cache: dict | None = None
        self._launcher.single_clicked.connect(self._toggle_workspace_sidebar)
        self._launcher.double_clicked.connect(self._on_launcher_double_clicked)
        self._launcher.action_requested.connect(self._handle_launcher_action)
        self._launcher.position_changed.connect(self._save_launcher_position)
        self._command_bar.submitted.connect(self._submit_command)
        self._command_bar.attach_clicked.connect(self._browse_attachment)
        self._command_bar.mic_clicked.connect(self._toggle_mute)
        self._command_bar.developer_clicked.connect(self._open_developer_mode_dialog)
        self._workspace_sidebar.command_submitted.connect(self._submit_command)
        self._workspace_sidebar.attach_requested.connect(self._browse_attachment)
        self._workspace_sidebar.mic_requested.connect(self._toggle_mute)
        self._workspace_sidebar.close_requested.connect(self._close_workspace_sidebar)
        self._win.minimized.connect(self._on_minimized)
        self._win.restored.connect(self._on_restored)
        self._win._state_sig.connect(self._sync_launcher_state)
        self._win._audio_level_sig.connect(self._launcher.set_audio_level)
        try:
            from core.clipboard_sentry import ClipboardSentry
            self._clip_sentry = ClipboardSentry(callback=self._on_clipboard_actionable)
            self._clip_sentry.start()
        except Exception:
            pass
        self._tray = QSystemTrayIcon(self._make_app_icon(), self._app)
        self._tray.setToolTip("Brahma Evo")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setContextMenu(self._build_tray_menu())
        self._tray.show()
        self._win._log_sig.connect(self._workspace_sidebar.append_log)
        self._win._task_workspace_sig.connect(self._workspace_sidebar.apply_task_workspace)
        # Some window layouts intentionally omit the inline chat workspace.  It is
        # optional, so do not let its absence prevent the application from starting.
        inline_workspace = getattr(self._win, "_inline_workspace", None)
        if inline_workspace is not None:
            self._win._task_workspace_sig.connect(inline_workspace.apply_task_workspace)
        self._on_discord_config_changed(self._win._load_discord_settings())
        launcher_pos = self._load_app_settings().get("launcher_pos")
        if isinstance(launcher_pos, (list, tuple)) and len(launcher_pos) == 2:
            try:
                self._launcher.move(int(launcher_pos[0]), int(launcher_pos[1]))
            except Exception:
                pass
        if bool(self._load_app_settings().get("show_workspace_on_startup", False)):
            self._workspace_sidebar.show_workspace(animate=False)
        else:
            self._workspace_sidebar.hide_workspace(animate=False)
        self.set_dashboard_page(getattr(self._win, "_current_page", "dashboard") == "dashboard")
        self._apply_developer_mode_ui()
        if show_immediately:
            self._launcher.hide()
            if self._should_play_boot_sequence():
                self.play_boot_sequence()
            else:
                self.show_main()
        else:
            self._show_floating_icon()
        self.root = _RootShim(self._app)

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

    def _sync_launcher_state(self, state: str):
        state = (state or "idle").strip().lower()
        detail = {
            "listening": "Ready",
            "thinking": "Thinking...",
            "processing": "Executing task...",
            "speaking": "Speaking",
            "muted": "Muted",
        }.get(state, "Ready")
        try:
            self._launcher.set_state(state, detail)
        except Exception:
            pass

    def _on_clipboard_actionable(self, category: str, content: str):
        label = {
            "error_traceback": "Traceback/Error copied — want an explanation?",
            "json_data": "JSON copied — format/validate?",
            "sql_query": "SQL Query copied — optimize/review?",
            "code_snippet": "Code snippet copied — review?",
        }.get(category, "Clipboard item copied")
        self._workspace_sidebar.record_chat_event({
            "role": "system",
            "text": f"💡 {label}",
            "source": "clipboard",
        })

    def _restart_app(self):
        try:
            args = _hidden_launch_args("--startup" if _launched_from_windows_startup() else "")
            args = [arg for arg in args if arg]
            kwargs = {"cwd": str(BASE_DIR)}
            if _OS == "Windows":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(args, **kwargs)
        except Exception as exc:
            self._win.write_log(f"ERR: Restart failed: {exc}")
        self._app.quit()

    def _on_panel_setting_toggle(self, key: str, value: bool):
        """Apply a toggle from the launcher control panel or the Settings card.

        Provider flags go through core.provider_router so the kill switch is
        honoured in one place; the rest persist to app_settings.json. Every step
        reports through _log rather than swallowing, because a silently-failing
        toggle is worse than a visible error -- the user cannot tell "off" from
        "broken".
        """
        persisted = False
        try:
            settings = self._load_app_settings()
            settings[key] = bool(value)
            self._save_app_settings(settings)
            persisted = True
        except Exception as exc:
            try:
                self._win.write_log(f"ERR: could not save {key}: {exc}")
            except Exception:
                pass
            return False

        try:
            if key.startswith("provider_enabled_"):
                from core import provider_router

                pid = key.replace("provider_enabled_", "")
                provider_router.set_enabled(pid, bool(value))
                # provider_router writes the file directly; invalidate the UI's
                # cached snapshot so a later save cannot re-write the stale
                # pre-toggle value over it.
                self._win._invalidate_app_settings_cache()
                chain = provider_router.describe()
                self._win.write_log(
                    f"SYS: Provider '{pid}' {'ENABLED' if value else 'DISABLED'}. "
                    f"Chain: {chain}"
                )
                return True
            if key == "enable_3d_background":
                bg = getattr(self._win, "_bg_widget", None)
                readback = {}
                if bg is not None and hasattr(bg, "set_animation_enabled"):
                    readback = bg.set_animation_enabled(bool(value)) or {}
                state = "enabled" if value else "disabled"
                if not readback.get("web_view"):
                    self._win.write_log(
                        f"ERR: 3D background {state} but no web view exists "
                        f"(WEB_ENGINE_AVAILABLE={readback.get('engine_available')}). "
                        f"Restart to apply."
                    )
                else:
                    self._win.write_log(
                        f"SYS: 3D background animation {state} "
                        f"(js={'issued' if readback.get('js_issued') else 'NOT issued'})."
                    )
                return True
            if key == "memory_extraction_enabled":
                self._win.write_log(
                    f"SYS: Background memory extraction "
                    f"{'ENABLED' if value else 'DISABLED'}."
                )
                return True
        except Exception as exc:
            try:
                self._win.write_log(f"ERR: toggle {key} saved but not applied: {exc}")
            except Exception:
                pass
            return False
        return persisted

    def _submit_command(self, text: str):
        self._win.submit_command(text)

    def _browse_attachment(self):
        self._win._browse_attachment()

    def _toggle_mute(self):
        self._win._toggle_mute()

    def _show_update_prompt(self, remote_hash: str):
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self._win,
            "Update Detected!",
            f"A new update is available on GitHub!\n\nDo you want to apply the update and restart the app?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            from core.updater import apply_update_and_restart
            # Show a simple overlay while updating
            self._win._set_status("Applying update from GitHub...")
            import threading
            threading.Thread(target=apply_update_and_restart, daemon=True).start()

    def _on_chat_event(self, event: dict):
        try:
            self._discord_service.mirror_chat_event(event or {})
        except Exception:
            pass
        try:
            self._workspace_sidebar.record_chat_event(event or {})
        except Exception:
            pass
        try:
            self._win._inline_workspace.record_chat_event(event or {})
        except Exception:
            pass

    def _on_discord_config_changed(self, settings: dict):
        settings = settings or {}
        enabled = bool(settings.get("enabled", False) or (settings.get("bot_token") or "").strip())
        token = (settings.get("bot_token") or "").strip()
        channel_id = (settings.get("channel_id") or "").strip()
        self._discord_service.set_target_channel_id(channel_id)
        if not enabled or not token:
            self._discord_service.stop()
            if not token:
                self._win.discord_status_changed.emit("Token required")
            else:
                self._win.discord_status_changed.emit("Discord bot disabled")
            return
        try:
            self._discord_service.start(token)
        except Exception as exc:
            msg = f"Discord error: {exc}"
            self._win.discord_status_changed.emit(msg)
            self._win._log_sig.emit(f"ERR: {msg}")

    def _open_app(self):
        self._command_bar.hide()
        self._workspace_sidebar.hide_workspace(animate=False)
        self.show_main()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    def set_muted(self, muted: bool):
        if bool(muted) != self._win._muted:
            self._win.set_muted_state(bool(muted))

    def set_muted_state(self, muted: bool, *, wakeword: bool = False):
        self._win.set_muted_state(bool(muted), wakeword=wakeword)

    @property
    def current_file(self) -> str | None:
        return self._win._current_file

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_attention_action(self):
        return self._win.on_attention_action

    @on_attention_action.setter
    def on_attention_action(self, cb):
        self._win.on_attention_action = cb

    @property
    def on_chat_event(self):
        return self._win.on_chat_event

    @on_chat_event.setter
    def on_chat_event(self, cb):
        self._win.on_chat_event = cb

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    # The Always On / On Call controls are declared on MainWindow. main.py
    # wires them through BrahmaUI, so they have to be reachable here -- they
    # were not, and the resulting AttributeError was swallowed by the caller,
    # leaving the whole feature silently inert.
    @property
    def live_mode_changed(self):
        return self._win.live_mode_changed

    @property
    def live_call_toggled(self):
        return self._win.live_call_toggled

    def set_audio_level(self, level: float):
        self._win.set_audio_level(level)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def show_confirm(self, title: str, detail: str = ""):
        self.w.show_confirm(title, detail)

    def hide_confirm(self):
        self.w.hide_confirm()

    def show_memory_inspector(self):
        self.w.show_memory_inspector()

    def hide_memory_inspector(self):
        self.w.hide_memory_inspector()

    def show_daily_briefing(self, text: str):
        self._win.show_daily_briefing(text)

    def hide_daily_briefing(self):
        self._win.hide_daily_briefing()

    def schedule_daily_briefing_hide(self):
        self._win.schedule_daily_briefing_hide()

    def submit_external_command(self, text: str, source: str = "discord"):
        self._win.submit_command(text, source=source)

    def notify_phone_connected(self):
        self._win.notify_phone_connected()

    def set_scanning(self, enabled: bool, text: str = ""):
        self._win.set_scanning(enabled, text)

    def show_circuit_hud(self, circuit_data: dict):
        self._win._circuit_hud_sig.emit(circuit_data or {})

    def show_call_screening(self, event: dict):
        self._win._call_screening_sig.emit(event or {})

    def update_call_screening_transcript(self, speaker: str, text: str):
        self._win._call_screening_sig.emit({
            "action": "transcript",
            "speaker": speaker,
            "text": text,
        })

    def hide_call_screening(self):
        self._win._call_screening_sig.emit({"action": "hide"})

    def show_attention_alert(self, event: dict):
        self._win._attention_sig.emit(event or {})

    def set_meeting_mode(self, enabled: bool, title: str = "", summary: str = "", answer: str = "", speech: str = ""):
        self._win.set_meeting_mode(enabled, title, summary, answer, speech)

    def begin_task_workspace(self, command: str, plan: list[str] | str | None = None, source: str = "local"):
        self._win._task_workspace_sig.emit({
            "action": "start",
            "command": command or "",
            "plan": plan or [],
            "source": source or "local",
        })

    def capture_screen_bytes(self) -> bytes:
        import threading
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QBuffer, QIODevice, Qt

        if threading.current_thread() == threading.main_thread():
            screen = QApplication.primaryScreen()
            if not screen: return b""
            pixmap = screen.grabWindow(0)
            pixmap = pixmap.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            pixmap.save(buffer, 'JPG', 55)
            return buffer.data().data()
        else:
            return self._win.capture_screen_threadsafe()

    def update_task_workspace(self, *, title: str | None = None, command: str | None = None, plan: list[str] | str | None = None,
                              status: str | None = None, output: str | None = None, percent: int | None = None,
                              footer: str | None = None, source: str | None = None):
        payload = {"action": "update"}
        if title is not None:
            payload["title"] = title
        if command is not None:
            payload["command"] = command
        if plan is not None:
            payload["plan"] = plan
        if status is not None:
            payload["status"] = status
        if output is not None:
            payload["output"] = output
        if percent is not None:
            payload["percent"] = percent
        if footer is not None:
            payload["footer"] = footer
        if source is not None:
            payload["source"] = source or "local"
        self._win._task_workspace_sig.emit(payload)

    def finish_task_workspace(self, result: str, status: str = "Task completed.", percent: int = 100):
        self._win._task_workspace_sig.emit({
            "action": "finish",
            "result": result or "Done.",
            "status": status or "Task completed.",
            "percent": percent,
        })

    def clear_task_workspace(self):
        self._win._task_workspace_sig.emit({"action": "clear"})

    def show_hud_operation(self, title: str, step: str, sources: list = None, tool: str = None, **kwargs):
        """Displays live operation telemetry on the right HUD wing."""
        if hasattr(self, "_win") and hasattr(self._win, "_hud_operation_sig"):
            payload = {
                "title": title,
                "step": step,
                "sources": sources or [],
                "tool": tool or ""
            }
            payload.update(kwargs)
            self._win._hud_operation_sig.emit(payload)

    def show_hud_deliverable(self, title: str, summary: str = "", bullets: list = None,
                             file_path: str = None, kind: str = "result", actions: list = None, data: dict = None, **kwargs):
        """Displays final results or file deliverables on the left HUD wing."""
        if hasattr(self, "_win") and hasattr(self._win, "_hud_deliverable_sig"):
            payload = {
                "title": title,
                "summary": summary or "",
                "bullets": bullets or [],
                "file_path": file_path or "",
                "kind": kind or "result",
                "actions": actions or [],
                "data": data or {}
            }
            payload.update(kwargs)
            self._win._hud_deliverable_sig.emit(payload)

    def show_content(self, title: str, body: str):
        """Universal rich content presenter. Automatically routes to the Brahma Holographic Left Deliverable Wing."""
        import re
        file_path = None
        m = re.search(r'([A-Za-z]:\\[^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', body)
        if m:
            cand = Path(m.group(1).strip())
            if cand.exists():
                file_path = str(cand.resolve())
        if not file_path:
            m2 = re.search(r'([^\s"\'<>`\r\n]+\.(?:pdf|docx|xlsx|pptx|png|jpg|mp4|py|html|json|txt))', body)
            if m2:
                cand2 = Path(m2.group(1).strip())
                if cand2.exists():
                    file_path = str(cand2.resolve())

        bullets = []
        for line in body.splitlines():
            line_str = line.strip()
            if (line_str.startswith(("-", "*", "•", ">")) or line_str.startswith(tuple("123456789."))) and len(line_str) > 2:
                clean_bullet = line_str.lstrip("-*•>0123456789. ").strip()
                if clean_bullet and not clean_bullet.startswith("#"):
                    bullets.append(clean_bullet)

        self.show_hud_deliverable(
            title=title,
            summary=body[:220] if not bullets else "",
            bullets=bullets[:5],
            file_path=file_path,
            kind="deliverable"
        )

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        # Every speech completion used to force LISTENING, which stomped the
        # STANDBY indicator right back the moment anything finished speaking.
        if not self.muted and self._win._state != "STANDBY":
            self.set_state("LISTENING")



