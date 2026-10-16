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


class BackgroundWidget(QWidget):
    _state_sig = pyqtSignal(str)
    _audio_sig = pyqtSignal(float)
    _emotion_sig = pyqtSignal(str, float)

    def __init__(self, image_path: Path | str | None = None, parent=None):
        super().__init__(parent)
        self._state_sig.connect(self._do_set_ai_state)
        self._audio_sig.connect(self._do_set_audio_level)
        self._emotion_sig.connect(self._do_set_avatar_emotion)
        self._last_audio_js_time = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        self.setMouseTracking(True)
        self._fallback_pixmap: QPixmap | None = None
        if image_path:
            try:
                path = Path(image_path)
                if path.exists():
                    self._fallback_pixmap = QPixmap(str(path))
            except Exception:
                self._fallback_pixmap = None
        self._web_view = None

        # Honour the saved preference at construction, otherwise the toggle
        # appeared not to persist across a restart: the page always booted with
        # animation running regardless of what was saved.
        _anim_on = True
        try:
            from memory import config_manager

            _anim_on = bool(config_manager.get_setting("enable_3d_background", True))
        except Exception:
            _anim_on = True

        if WEB_ENGINE_AVAILABLE:
            self._init_web_engine()
            if not _anim_on:
                self.set_animation_enabled(False)
        self._animation_enabled = _anim_on

    def set_animation_enabled(self, enabled: bool, reason: str = "") -> dict:
        """Start or stop the animated web background.

        Returns a readback dict so the caller can report honestly rather than
        assuming success: hiding the widget alone was NOT enough, because the
        page kept requesting animation frames behind it. The page exposes
        __pauseAnimation/__resumeAnimation for exactly this, so we drive that
        first and treat it as the source of truth.

        Also hides/shows the view, because a hidden QWebEngineView may still be
        composited by the browser process on some Qt builds.
        """
        readback = {"engine_available": bool(WEB_ENGINE_AVAILABLE),
                    "web_view": self._web_view is not None,
                    "js_issued": False}
        if not WEB_ENGINE_AVAILABLE:
            return readback

        if enabled:
            if self._web_view is None:
                self._init_web_engine()
            if self._web_view is not None:
                # Show BEFORE resuming: a hidden view may not run queued JS.
                self._web_view.show()
                self._web_view.raise_()
                try:
                    self._web_view.page().runJavaScript(
                        "window.__resumeAnimation && window.__resumeAnimation();"
                    )
                    readback["js_issued"] = True
                except Exception as exc:
                    readback["js_error"] = str(exc)
        else:
            if self._web_view is not None:
                try:
                    self._web_view.page().runJavaScript(
                        "window.__pauseAnimation && window.__pauseAnimation();"
                    )
                    readback["js_issued"] = True
                except Exception as exc:
                    readback["js_error"] = str(exc)
                self._web_view.hide()
            self.update()
        return readback

    def _avatar_page_path(self) -> str:
        """Which avatar document to show. Falls back to the 3D background.

        core owns which backend is selected; this only asks it for a path, so
        that neither side has to know about the other's internals.
        """
        try:
            from core import avatar_runtime

            return avatar_runtime.active_page_path()
        except Exception:
            return str(BASE_DIR / "assets" / "web_background" / "index.html")

    def _avatar_model_url(self) -> str:
        try:
            from core import avatar_runtime

            return avatar_runtime.active_model_url()
        except Exception:
            return ""

    def refresh_avatar_page(self) -> bool:
        """Re-point the web view at the current backend's document."""
        try:
            return self.set_avatar_page(self._avatar_page_path(),
                                        self._avatar_model_url())
        except Exception:
            return False

    def _init_web_engine(self):
        if self._web_view is not None:
            return
        try:
            html_path = Path(self._avatar_page_path())
            if not html_path.exists():
                return
            self._web_view = QWebEngineView(self)
            st = self._web_view.settings()
            try:
                st.setAttribute(st.WebAttribute.WebGLEnabled, True)
                st.setAttribute(st.WebAttribute.JavascriptEnabled, True)
                st.setAttribute(st.WebAttribute.LocalContentCanAccessRemoteUrls, True)
                st.setAttribute(st.WebAttribute.LocalContentCanAccessFileUrls, True)
                st.setAttribute(st.WebAttribute.Accelerated2dCanvasEnabled, True)
                st.setAttribute(st.WebAttribute.ScrollAnimatorEnabled, False)
            except Exception:
                pass

            self._web_view.setStyleSheet("background: #020306;")
            self._web_view.page().setBackgroundColor(QColor("#020306"))
            self._web_view.loadFinished.connect(self._on_web_loaded)

            file_url = QUrl.fromLocalFile(str(html_path.resolve()))
            model_url = self._avatar_model_url()
            if model_url:
                from urllib.parse import quote
                file_url.setQuery("model=" + quote(model_url, safe=":/"))
            self._web_view.load(file_url)

            w = max(self.width(), 800)
            h = max(self.height(), 600)
            self._web_view.setGeometry(0, 0, w, h)
            self._web_view.lower()
            self._web_view.show()
        except Exception as e:
            print(f"[BackgroundWidget] Init web engine error: {e}")
            self._web_view = None

    def _on_web_loaded(self, ok: bool):
        if ok and self._web_view:
            if self.width() > 0 and self.height() > 0:
                self._web_view.setGeometry(self.rect())
                self._web_view.lower()
            st = getattr(self, "_last_state", "IDLE") or "IDLE"
            self._do_set_ai_state(st)
            # The page only exists from here on, so this is the first moment a
            # mood emitted during boot can actually be rendered. Without it the
            # first reply's emotion is silently lost on every launch.
            try:
                from core import avatar_runtime

                avatar_runtime.flush_pending_mood()
            except Exception:
                pass
        elif not ok:
            print("[BackgroundWidget] Background WebEngine failed to load, retrying in 250ms...")
            html_path = Path(self._avatar_page_path())
            if html_path.exists() and self._web_view:
                QTimer.singleShot(250, lambda: self._web_view.load(QUrl.fromLocalFile(str(html_path.resolve()))))

    def _load_background(self) -> None:
        pass

    def _apply_theme_to_html(self, html_content: str) -> str:
        import colorsys
        import re
        
        try:
            r = int(C.PRI[1:3], 16)
            g = int(C.PRI[3:5], 16)
            b = int(C.PRI[5:7], 16)
            target_h, _, _ = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)
        except:
            return html_content
            
        def map_hex(match):
            old_hex = match.group(0)
            prefix = old_hex[:2]
            h_str = old_hex[2:]
            if len(h_str) != 6: return old_hex
            try:
                or_r, or_g, or_b = int(h_str[:2], 16), int(h_str[2:4], 16), int(h_str[4:], 16)
                _, or_l, or_s = colorsys.rgb_to_hls(or_r/255.0, or_g/255.0, or_b/255.0)
                n_r, n_g, n_b = colorsys.hls_to_rgb(target_h, or_l, or_s)
                return f"{prefix}{int(n_r*255):02x}{int(n_g*255):02x}{int(n_b*255):02x}"
            except:
                return old_hex
                
        gold_hexes = [r"0xffbe1a", r"0xaa8010", r"0x040302", r"0x553f05", r"0xffea80", r"0xfff2a3", r"0xffe680", r"0xd4af37", r"0x3b2e0c"]
        for gh in gold_hexes:
            html_content = re.sub(gh, map_hex, html_content, flags=re.IGNORECASE)
            
        return html_content

    def set_ai_state(self, state: str) -> None:
        try:
            self._state_sig.emit(str(state or "IDLE"))
        except Exception:
            pass

    def _do_set_ai_state(self, state: str) -> None:
        if self._web_view:
            try:
                st = (state or "IDLE").strip().replace("'", "\\'")
                page = self._web_view.page()
                if page:
                    page.runJavaScript(f"if(window.setBrahmaState) window.setBrahmaState('{st}');")
            except Exception:
                pass

    def set_audio_level(self, level: float) -> None:
        try:
            self._audio_sig.emit(float(level))
        except Exception:
            pass

    def _do_set_audio_level(self, level: float) -> None:
        now = time.monotonic()
        if (now - self._last_audio_js_time) < 0.016:  # Ultra-smooth 60 FPS WebEngine bridge
            return
        self._last_audio_js_time = now
        if self._web_view:
            try:
                lv = max(0.0, min(1.0, float(level)))
                page = self._web_view.page()
                if page:
                    page.runJavaScript(f"if(window.setAudioLevel) window.setAudioLevel({lv:.3f});")
            except Exception:
                pass

    def set_avatar_page(self, page_path: str, model_url: str = "") -> bool:
        """Point the web view at a different avatar document.

        The avatar backends are separate pages that expose the same hooks, so
        switching renderer is a URL swap rather than a mode flag. Done here
        rather than in core, because QWebEngine is a Qt concern and core must
        not import the UI.

        Returns True when a reload was actually issued, so callers do not have
        to assume it took.
        """
        if not page_path or not os.path.isfile(page_path):
            return False
        if self._web_view is None:
            return False
        try:
            url = QUrl.fromLocalFile(str(Path(page_path).resolve()))
            if model_url:
                from urllib.parse import quote
                url.setQuery("model=" + quote(str(model_url), safe=":/"))
            self._web_view.load(url)
            return True
        except Exception:
            return False

    def set_avatar_emotion(self, tag: str, intensity: float = 1.0) -> bool:
        """Queue one emotion for the 3D background. Returns whether it was accepted.

        Emits a signal rather than touching the page here. The tag can arrive on
        a reply worker thread or the Gemini Live asyncio loop, and
        QWebEngineView.runJavaScript is only safe on the GUI thread — a direct
        call crashed the app the moment a tag landed. The signal is queued to
        the GUI thread, which is why the state and audio hooks use the same
        pattern. "Accepted" means it reached this channel, not that pixels moved.
        """
        if self._web_view is None:
            return False
        try:
            try:
                iv = max(0.0, min(2.0, float(intensity)))
            except (TypeError, ValueError):
                iv = 1.0
            self._emotion_sig.emit(str(tag or ""), iv)
            return True
        except Exception:
            return False

    def _do_set_avatar_emotion(self, tag: str, intensity: float) -> None:
        if self._web_view is None:
            return
        try:
            js = (f"window.setAvatarEmotion && "
                  f"window.setAvatarEmotion({json.dumps(str(tag or ''))}, {float(intensity):.3f});")
            page = self._web_view.page()
            if page is None:
                return
            page.runJavaScript(js)
        except Exception:
            pass

    def set_pointer_norm(self, nx: float, ny: float) -> None:
        if self._web_view:
            try:
                page = self._web_view.page()
                if page:
                    page.runJavaScript(f"if(window.setPointerNorm) window.setPointerNorm({nx:.4f}, {ny:.4f});")
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        if self._web_view is None and WEB_ENGINE_AVAILABLE:
            self._init_web_engine()
        elif self._web_view and self.width() > 0 and self.height() > 0:
            self._web_view.setGeometry(self.rect())
            self._web_view.lower()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._web_view is None and WEB_ENGINE_AVAILABLE:
            self._init_web_engine()
        elif self._web_view and self.width() > 0 and self.height() > 0:
            self._web_view.setGeometry(self.rect())
            self._web_view.lower()

    def paintEvent(self, event):
        if not self._web_view:
            painter = QPainter(self)
            rect = self.rect()
            if self._fallback_pixmap and not self._fallback_pixmap.isNull():
                painter.fillRect(rect, QColor(0, 0, 0))
                scaled = self._fallback_pixmap.scaled(
                    rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = rect.x() + (rect.width() - scaled.width()) // 2
                y = rect.y() + (rect.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
            else:
                painter.fillRect(rect, QColor(0, 0, 0))
            painter.end()
            return
        super().paintEvent(event)


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


def _quiet_run(*args, **kwargs):
    if _OS == "Windows":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(*args, **kwargs)


def _quote_cmd_arg(path: str) -> str:
    return f'"{path}"'


def _hidden_launch_args(*extra_args: str) -> list[str]:
    main_py = BASE_DIR / "main.py"
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return [str(exe), *extra_args]
    venv_pythonw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return [str(venv_pythonw), str(main_py), *extra_args]
    return [str(sys.executable), str(main_py), *extra_args]

def _startup_run_value() -> str:
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return f'{_quote_cmd_arg(str(exe))} --startup'
    main_py = BASE_DIR / "main.py"
    venv_pythonw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return f'{_quote_cmd_arg(str(venv_pythonw))} {_quote_cmd_arg(str(main_py))} --startup'
    return f'{_quote_cmd_arg(sys.executable)} {_quote_cmd_arg(str(main_py))} --startup'


def _startup_registry_key():
    if platform.system() != "Windows":
        return None
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def _current_boot_stamp() -> int:
    try:
        return int(psutil.boot_time())
    except Exception:
        return int(time.time())


def _launched_from_windows_startup() -> bool:
    return any(str(arg).strip().lower() == "--startup" for arg in sys.argv[1:])


def _default_app_settings() -> dict:
    return {
        "startup_animation_enabled": True,
        "last_boot_stamp": 0,
        "boot_sequence_played": True,
        "show_workspace_on_startup": False,
        "launcher_pos": None,
        "launch_minimized": False,
        "check_updates_on_startup": True,
        "default_ai_provider": "Gemini",
        "auto_provider_switch": True,
        "local_ai_url": "http://localhost:11434/v1",
        "local_ai_model": "llama3.2",
        "local_api_key": "",
        "custom_ai_url": "",
        "custom_ai_model": "",
        "custom_api_key": "",
        "custom_mcp_servers": [],
        # De-facto standard MCP config shape, read as a fallback by
        # core.mcp_manager when custom_mcp_servers is empty, so a config from
        # another MCP client works unchanged: {"name": {command,args,env}} for
        # stdio or {"name": {url,headers}} for a remote server.
        "mcpServers": {},
        # Tool & Skill Index. Budget is the number of function declarations sent
        # on the Gemini Live setup frame; the provider hard-caps this at 128, so
        # the default stays far below. Everything else is reachable on demand
        # through the search_tools / call_tool meta-tools.
        "tool_index_enabled": True,
        "tool_index_budget": 24,
        # Per-provider kill switches for the failover chain (core/provider_router.py).
        # All default True so behaviour is unchanged until someone toggles one.
        # Switch OpenRouter off to guarantee zero cloud egress from background work.
        "provider_enabled_gemini": True,
        "provider_enabled_openrouter": True,
        "provider_enabled_local": True,
        "provider_enabled_custom": True,
        # Background memory extraction. Deliberately independent of the selected
        # provider: running locally is when you still want the memory graph built.
        "memory_extraction_enabled": True,
        # 3D background animation; costs GPU/CPU every frame, so it is switchable.
        "enable_3d_background": True,
        # Speech engines for the modular (non-Gemini) speech pipeline. Both
        # default to the previous cloud behaviour so nothing changes until the
        # user picks a local engine; see core/speech_stt.py and core/speech_tts.py.
        "speech_stt_engine": "google",
        "speech_whisper_model": "base.en",
        "speech_tts_engine": "edge",
        "speech_tts_voice": "",
        "attention_message_prompts": True,
        "attention_call_prompts": True,
        "developer_mode_enabled": False,
        "developer_mode_workspace": "",
    }


def _default_discord_settings() -> dict:
    return {
        "bot_token": "",
        "enabled": False,
        "channel_id": "",
    }

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


def _active_net_label() -> str:
    try:
        stats = psutil.net_if_stats()
        active = []
        for name, info in stats.items():
            if getattr(info, "isup", False):
                active.append(name)
        if active:
            return active[0]
    except Exception:
        pass
    return "No active adapter"


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0â€“100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

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


class DeveloperModeDialog(QDialog):
    def __init__(self, parent=None, settings: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Developer Mode")
        self.setMinimumWidth(420)
        self.setStyleSheet(f"""
            QDialog {{ background: rgba(8,10,14,235); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}
            QLabel {{ color: {C.TEXT}; }}
            QLineEdit {{
                background: rgba(16,16,16,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
                padding: 8px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
            QPushButton {{
                background: rgba(18,18,18,240);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
                padding: 8px 12px;
            }}
            QPushButton:hover {{ background: rgba(28,28,28,245); border: 1px solid {C.PRI}; }}
            QCheckBox {{ color: {C.TEXT}; }}
        """)

        self._settings = dict(settings or {})
        self._enabled = bool(self._settings.get("developer_mode_enabled", False))
        fallback_workspace = str(Path(__file__).resolve().parent)
        self._workspace = str(self._settings.get("developer_mode_workspace", "") or fallback_workspace)

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Developer Co-pilot")
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI};")
        root.addWidget(title)

        desc = QLabel("Pick a workspace folder Brahma Evo should use when building websites or other workspace-based tasks.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {C.TEXT_DIM};")
        root.addWidget(desc)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self._workspace_edit = QLineEdit(self._workspace)
        self._workspace_edit.setPlaceholderText("Select a folder...")
        self._workspace_edit.setReadOnly(True)
        folder_row.addWidget(self._workspace_edit, stretch=1)

        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)

        self._enabled_box = QCheckBox("Turn developer mode on")
        self._enabled_box.setChecked(self._enabled)
        root.addWidget(self._enabled_box)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(self._save_and_close)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        root.addLayout(btn_row)

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select developer workspace", self._workspace or str(BASE_DIR))
        if path:
            self._workspace_edit.setText(path)

    def _save_and_close(self):
        self._settings["developer_mode_enabled"] = bool(self._enabled_box.isChecked())
        self._settings["developer_mode_workspace"] = self._workspace_edit.text().strip()
        self.accept()

    def get_settings(self) -> dict:
        return dict(self._settings)


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


class MainWindow(QMainWindow):
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

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _settings_file_stamp(self):
        """Identity of the settings file as it is on disk right now."""
        try:
            if APP_SETTINGS_FILE.exists():
                st = APP_SETTINGS_FILE.stat()
                return (st.st_mtime_ns, st.st_size)
        except Exception:
            pass
        return None

    def _load_app_settings(self) -> dict:
        # A plain cache is not safe here: core/provider_router.py and
        # core/instruction_injector.py write this same file through
        # memory.config_manager without knowing this cache exists. Serving the
        # cached snapshot after such a write makes the UI believe the old value,
        # and the next save writes that old value back - which is how disabling
        # OpenRouter reverted after an unrelated save. Re-read whenever the file
        # on disk has changed, so the two owners can never diverge.
        stamp = self._settings_file_stamp()
        if self._app_settings_cache is not None and stamp == getattr(
            self, "_app_settings_cache_stamp", None
        ):
            return dict(self._app_settings_cache)
        settings = _default_app_settings()
        if APP_SETTINGS_FILE.exists():
            try:
                data = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:
                pass
        self._app_settings_cache = dict(settings)
        self._app_settings_cache_stamp = self._settings_file_stamp()
        return dict(settings)

    def _invalidate_app_settings_cache(self):
        """Force the next load to re-read the file.

        Settings have more than one owner. The cards write through here, but
        core/provider_router.py and core/instruction_injector.py write the same
        file through memory.config_manager and never touch this cache. Left
        stale, the cache carries the OLD value and a later save writes it back,
        silently reverting the other component's change. That is precisely how
        disabling OpenRouter kept coming back after an unrelated save.
        """
        self._app_settings_cache = None

    def _save_app_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Merge over what is actually on disk instead of trusting the caller's
        # snapshot: another component may have written a key since this dict
        # was loaded, and a blind replace would revert it.
        merged = dict(settings)
        if APP_SETTINGS_FILE.exists():
            try:
                on_disk = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(on_disk, dict):
                    merged = on_disk
                    merged.update(settings)
            except Exception:
                pass
        atomic_write_json(APP_SETTINGS_FILE, merged)
        self._app_settings_cache = dict(merged)

    def _startup_animation_enabled(self) -> bool:
        if platform.system() != "Windows":
            return False
        return bool(self._load_app_settings().get("startup_animation_enabled", True))

    def _set_startup_animation_enabled(self, enabled: bool) -> bool:
        try:
            settings = self._load_app_settings()
            settings["startup_animation_enabled"] = bool(enabled)
            settings["last_boot_stamp"] = _current_boot_stamp()
            self._save_app_settings(settings)
            return True
        except Exception as e:
            self._log.append_log("ERR: startup animation setting failed: %s" % e)
            return False

    def _refresh_startup_animation_button(self):
        if not hasattr(self, "_startup_anim_btn"):
            return
        if platform.system() != "Windows":
            self._startup_anim_btn.setText("Startup Animation (Windows only)")
            self._startup_anim_btn.setEnabled(False)
            return
        if self._startup_animation_enabled():
            self._startup_anim_btn.setText("Disable Startup Animation")
        else:
            self._startup_anim_btn.setText("Enable Startup Animation")

    def _toggle_startup_animation(self):
        if platform.system() != "Windows":
            return
        enabled = not self._startup_animation_enabled()
        if self._set_startup_animation_enabled(enabled):
            self._refresh_startup_animation_button()
            state = "enabled" if enabled else "disabled"
            self._log.append_log("SYS: Startup animation %s." % state)

    def _load_discord_settings(self) -> dict:
        settings = _default_discord_settings()
        if DISCORD_SETTINGS_FILE.exists():
            try:
                data = json.loads(DISCORD_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:
                pass
        if (settings.get("bot_token") or "").strip():
            settings["enabled"] = True
        return dict(settings)

    def _save_discord_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        DISCORD_SETTINGS_FILE.write_text(json.dumps(settings, indent=4), encoding="utf-8")

    def _emit_discord_settings(self):
        if not hasattr(self, "_discord_token_input"):
            return
        settings = {
            "bot_token": self._discord_token_input.text().strip(),
            "enabled": bool(getattr(self, "_discord_enabled", False)),
            "channel_id": self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else "",
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._refresh_discord_card()

    def _refresh_discord_card(self, note: str = ""):
        if not hasattr(self, "_discord_status_lbl"):
            return
        token = self._discord_token_input.text().strip() if hasattr(self, "_discord_token_input") else ""
        enabled = bool(getattr(self, "_discord_enabled", False))
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        if note:
            status = note
            color = C.PRI if "error" in note.lower() or "missing" in note.lower() else C.TEXT_MED
        elif not token:
            status = "Token required"
            color = C.PRI
        elif not channel_id:
            status = "Token saved - channel optional"
            color = C.TEXT_MED
        elif enabled:
            status = "Bot enabled"
            color = C.GREEN
        else:
            status = "Bot disabled"
            color = C.TEXT_MED
        self._discord_status_lbl.setText(status)
        self._discord_status_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        if hasattr(self, "_discord_start_btn"):
            self._discord_start_btn.setText("Start Discord Bot")
        if hasattr(self, "_discord_stop_btn"):
            self._discord_stop_btn.setText("Stop Discord Bot")
        if hasattr(self, "_discord_save_btn"):
            self._discord_save_btn.setText("Save Settings")

    def _save_discord_token(self):
        if not hasattr(self, "_discord_token_input"):
            return
        token = self._discord_token_input.text().strip()
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        settings = self._load_discord_settings()
        settings["bot_token"] = token
        settings["channel_id"] = channel_id
        settings["enabled"] = bool(token)
        self._discord_enabled = bool(token)
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        if token:
            self._log.append_log("SYS: Discord bot token saved and bot enabled.")
            self._refresh_discord_card("Token saved")
        else:
            self._log.append_log("SYS: Discord bot token cleared.")
            self._discord_enabled = False
            self._refresh_discord_card("Token required")

    def _start_discord_bot(self):
        if not hasattr(self, "_discord_token_input"):
            return
        token = self._discord_token_input.text().strip()
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        if not token:
            self._discord_enabled = False
            self._save_discord_token()
            return
        self._discord_enabled = True
        settings = {
            "bot_token": token,
            "enabled": True,
            "channel_id": channel_id,
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._log.append_log("SYS: Discord bot started.")
        self._refresh_discord_card()

    def _stop_discord_bot(self):
        if not hasattr(self, "_discord_token_input"):
            return
        token = self._discord_token_input.text().strip()
        channel_id = self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else ""
        self._discord_enabled = False
        settings = {
            "bot_token": token,
            "enabled": False,
            "channel_id": channel_id,
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._log.append_log("SYS: Discord bot stopped.")
        self._refresh_discord_card()

    def _load_api_defaults(self) -> dict:
        if not API_FILE.exists():
            return {
                "gemini_api_key": "",
                "openrouter_api_key": "",
                "anthropic_api_key": "",
                "os_system": platform.system(),
            }
        try:
            data = json.loads(API_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("anthropic_api_key", "")
                return data
        except Exception:
            pass
        return {
            "gemini_api_key": "",
            "openrouter_api_key": "",
            "anthropic_api_key": "",
            "os_system": platform.system(),
        }

    def _startup_enabled(self) -> bool:
        if platform.system() != "Windows":
            return False
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _startup_registry_key(),
                0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            ) as key:
                try:
                    value, _ = winreg.QueryValueEx(key, "Brahma Evo")
                    run_value = _startup_run_value()
                    if value != run_value:
                        winreg.SetValueEx(key, "Brahma Evo", 0, winreg.REG_SZ, run_value)
                    return bool(value)
                except FileNotFoundError:
                    return False
        except Exception:
            return False

    def _set_startup_enabled(self, enabled: bool) -> bool:
        if platform.system() != "Windows":
            return False
        run_value = _startup_run_value()
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _startup_registry_key()) as key:
                if enabled:
                    winreg.SetValueEx(key, "Brahma Evo", 0, winreg.REG_SZ, run_value)
                else:
                    try:
                        winreg.DeleteValue(key, "Brahma Evo")
                    except FileNotFoundError:
                        pass
            return True
        except Exception as e:
            self._log.append_log(f"ERR: startup setting failed: {e}")
            return False

    def _refresh_startup_button(self):
        if not hasattr(self, "_startup_btn"):
            return
        if platform.system() != "Windows":
            self._startup_btn.setText("Start on Startup (Windows only)")
            self._startup_btn.setEnabled(False)
            return
        if self._startup_enabled():
            self._startup_btn.setText("Start on Startup: ON")
        else:
            self._startup_btn.setText("Start on Startup: OFF")

    def _toggle_startup(self):
        if platform.system() != "Windows":
            return
        enabled = not self._startup_enabled()
        if self._set_startup_enabled(enabled):
            self._refresh_startup_button()
            state = "enabled" if enabled else "disabled"
            self._log.append_log(f"SYS: Windows startup {state}.")

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

    def _tick_clock(self):
        if hasattr(self, "_clock_lbl") and self._clock_lbl is not None:
            try:
                self._clock_lbl.setText(time.strftime("%H:%M"))
            except Exception:
                pass
        if hasattr(self, "_date_lbl") and self._date_lbl is not None:
            try:
                self._date_lbl.setText(time.strftime("%A • %d %b"))
            except Exception:
                pass

    def _update_metrics(self):
        if not hasattr(self, "_bar_cpu"):
            return
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        if hasattr(self, "_bar_cpu"):
            self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")
        if hasattr(self, "_stat_cpu"):
            cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 0
            cpu_threads = psutil.cpu_count(logical=True) or cpu_cores
            self._stat_cpu.set_value(
                f"{cpu:.0f}%",
                int(cpu),
                f"{cpu_threads} threads / {cpu_cores or cpu_threads} cores",
            )

        # MEM
        mem = snap["mem"]
        if hasattr(self, "_bar_mem"):
            self._bar_mem.set_value(mem, f"{mem:.0f}%")
        
        if hasattr(self, "_uptime_lbl"):
            try:
                boot = psutil.boot_time()
                uptime_min = int((time.time() - boot) / 60)
                self._uptime_lbl.setText(f"Up: {uptime_min // 60}h {uptime_min % 60}m")
            except: pass

        if hasattr(self, "_stat_mem"):
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)
            self._stat_mem.set_value(
                f"{mem:.0f}%",
                int(mem),
                f"{used_gb:.1f} GB / {total_gb:.1f} GB used",
            )

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        if hasattr(self, "_bar_net"):
            self._bar_net.set_value(net_pct, net_str)
        if hasattr(self, "_stat_net"):
            self._stat_net.set_value(
                net_str if net >= 1 else "ONLINE",
                int(net_pct),
                _active_net_label(),
            )

        # GPU
        gpu = snap["gpu"]
        if hasattr(self, "_bar_gpu"):
            if gpu >= 0:
                self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
            else:
                self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if hasattr(self, "_bar_tmp"):
            if tmp >= 0:
                tmp_pct = min(100, (tmp / 100) * 100)
                self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
            else:
                self._bar_tmp.set_value(0, "N/A")
        if hasattr(self, "_stat_cam"):
            cam_on = _camera_available()
            self._stat_cam.set_value(
                "ON" if cam_on else "OFF",
                100 if cam_on else 0,
                "Webcam detected" if cam_on else "No camera found",
            )

        if hasattr(self, "_uptime_lbl"):
            try:
                boot_t  = psutil.boot_time()
                elapsed = time.time() - boot_t
                h = int(elapsed // 3600)
                m = int((elapsed % 3600) // 60)
                self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
            except Exception:
                self._uptime_lbl.setText("UP  --:--")

        if hasattr(self, "_proc_lbl"):
            try:
                proc_count = len(psutil.pids())
                self._proc_lbl.setText(f"PROC  {proc_count}")
            except Exception:
                self._proc_lbl.setText("PROC  --")


    def _browse_attachment(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach a file to Brahma Evo", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._on_file_selected(path)

    def _toggle_mute(self):
        self._muted = not self._muted
        self._wakeword_listening = self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
            sound_mgr.play_listening_stop()
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")
            sound_mgr.play_listening_start()

    def set_muted_state(self, muted: bool, *, wakeword: bool = False):
        muted = bool(muted)
        if muted == self._muted and not wakeword:
            return
        self._muted = muted
        self.hud.muted = self._muted
        self._wakeword_listening = bool(muted)
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
            sound_mgr.play_listening_stop()
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")
            sound_mgr.play_listening_start()

    def _style_mute_btn(self):
        if not hasattr(self, "_mute_btn") or self._mute_btn is None:
            return
        if self._muted:
            self._mute_btn.setText("Muted")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #1a1010; color: {C.RED};
                    border: 1px solid {C.RED}; border-radius: 16px;
                }}
            """)
        else:
            self._mute_btn.setText("Mic")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(16,16,16,235); color: {C.WHITE};
                    border: 1px solid {C.BORDER_B}; border-radius: 16px;
                }}
                QPushButton:hover {{ border: 1px solid {C.WHITE}; }}
            """)

    def _send(self, txt: str = ""):
        if not txt:
            txt = self._input.text().strip()
            self._input.clear()
        if not txt:
            return
        self.submit_command(txt)

    def submit_command(self, txt: str, source: str = "local"):
        txt = (txt or "").strip()
        if not txt:
            return
        self._chat_source_queue.append(source or "local")
        if hasattr(self, "_command_card"):
            preview = txt[:60] + ("…" if len(txt) > 60 else "")
            self._command_card.set_body(preview)
            self._command_card.hide()
        if hasattr(self, "_result_card"):
            self._result_card.set_body("Waiting for reply...")
            self._result_card.hide()
        self._restart_card_hide_timer()
        self._log_sig.emit(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt, source or "local"), daemon=True).start()

    def _on_log_text(self, text: str):
        self._log.append_log(text)
        raw = (text or "").strip()
        low = raw.lower()
        if hasattr(self, "_result_card") and low.startswith("you:"):
            user_msg = raw.split(":", 1)[1].strip()
            source = self._chat_source_queue[0] if self._chat_source_queue else "local"
            if self.on_chat_event and user_msg:
                try:
                    self.on_chat_event({"role": "user", "text": user_msg, "source": source})
                except Exception:
                    pass
        if hasattr(self, "_result_card") and low.startswith("brahma evo:"):
            reply = raw.split(":", 1)[1].strip()
            self._result_card.set_body(reply[:80] + ("…" if len(reply) > 80 else ""))
            self._result_card.hide()
            self._restart_card_hide_timer()
            source = self._chat_source_queue[0] if self._chat_source_queue else "local"
            if self.on_chat_event and reply:
                try:
                    self.on_chat_event({"role": "assistant", "text": reply, "source": source})
                except Exception:
                    pass
            if self._chat_source_queue:
                self._chat_source_queue.popleft()
        elif hasattr(self, "_result_card") and low.startswith("err:"):
            self._result_card.set_body(raw.split(":", 1)[1].strip())
            self._result_card.hide()
            self._restart_card_hide_timer()
            source = self._chat_source_queue[0] if self._chat_source_queue else "local"
            if self.on_chat_event:
                try:
                    self.on_chat_event({"role": "system", "text": raw.split(":", 1)[1].strip(), "source": source})
                except Exception:
                    pass
            if self._chat_source_queue:
                self._chat_source_queue.popleft()

    def _restart_card_hide_timer(self):
        if hasattr(self, "_card_hide_tmr"):
            self._card_hide_tmr.start(5000)

    def _hide_command_cards(self):
        if hasattr(self, "_command_card"):
            self._command_card.hide()
        if hasattr(self, "_result_card"):
            self._result_card.hide()
        if hasattr(self, "_hud_result_wing"):
            self._hud_result_wing.dismiss()
        if hasattr(self, "_hud_telemetry_wing"):
            self._hud_telemetry_wing.dismiss()

    def _apply_hud_operation(self, data: dict):
        if hasattr(self, "_hud_telemetry_wing"):
            self._hud_telemetry_wing.show_operation(
                title=data.get("title") or "OPERATION IN PROGRESS",
                step=data.get("step") or "Processing...",
                sources=data.get("sources") or [],
                tool=data.get("tool") or ""
            )

    def _apply_hud_deliverable(self, data: dict):
        if hasattr(self, "_hud_result_wing"):
            self._hud_result_wing.show_deliverable(
                title=data.get("title") or "MISSION DELIVERABLE",
                summary=data.get("summary") or "",
                bullets=data.get("bullets") or [],
                file_path=data.get("file_path"),
                kind=data.get("kind") or "result",
                actions=data.get("actions") or [],
                data=data.get("data") or {}
            )

    def _apply_daily_briefing(self, payload):
        action, data = payload if isinstance(payload, tuple) else ("show", payload)
        if action == "hide":
            if getattr(self, "_briefing_overlay", None):
                try:
                    self._briefing_overlay.hide()
                    self._briefing_overlay.deleteLater()
                except Exception:
                    pass
                self._briefing_overlay = None
            if hasattr(self, "_briefing_card"):
                self._briefing_card.hide()
            if hasattr(self, "_smart_devices_section"):
                self._smart_devices_section.show()
            return

        # Legacy label support if present
        if hasattr(self, "_briefing_text_lbl"):
            summary_txt = data if isinstance(data, str) else (data.get("spoken_narrative", "") if isinstance(data, dict) else str(data))
            clean_text = " ".join(str(summary_txt or "").split())
            self._briefing_text_lbl.setText(clean_text[:360] + ("..." if len(clean_text) > 360 else ""))

        # Holographic HUD card overlay
        if getattr(self, "_briefing_overlay", None):
            try:
                self._briefing_overlay.deleteLater()
            except Exception:
                pass
            self._briefing_overlay = None

        overlay = DailyBriefingOverlay(data, parent=self)
        overlay.closed.connect(lambda: setattr(self, "_briefing_overlay", None))
        self._briefing_overlay = overlay
        self._position_briefing_overlay()
        overlay.show()
        overlay.raise_()

        try:
            from sound_manager import sound_mgr
            sound_mgr.play_sfx("telemetry_chirp")
        except Exception:
            pass

    def _position_briefing_overlay(self):
        if not getattr(self, "_briefing_overlay", None):
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._briefing_overlay.width()) // 2)
        y = max(16, (geo.height() - self._briefing_overlay.height()) // 2)
        self._briefing_overlay.move(x, y)

    def _hide_daily_briefing_card(self):
        self._apply_daily_briefing(("hide", None))

    def _schedule_daily_briefing_hide(self):
        if getattr(self, "_briefing_overlay", None):
            self._briefing_overlay._remaining_secs = min(getattr(self._briefing_overlay, "_remaining_secs", 30), 10)

    def show_daily_briefing(self, text):
        self._briefing_sig.emit(("show", text))

    def hide_daily_briefing(self):
        self._briefing_sig.emit(("hide", None))

    def schedule_daily_briefing_hide(self):
        self._schedule_daily_briefing_hide()

    def show_confirm(self, title: str, detail: str = ""):
        self._confirm_sig.emit("show", title, detail)

    def hide_confirm(self):
        self._confirm_sig.emit("hide", "", "")

    def _apply_confirm_overlay(self, action: str, title: str = "", detail: str = ""):
        if action == "hide":
            if getattr(self, "_confirm_overlay", None):
                try:
                    self._confirm_overlay.deleteLater()
                except Exception:
                    pass
                self._confirm_overlay = None
            return

        if action == "show":
            if getattr(self, "_confirm_overlay", None):
                try:
                    self._confirm_overlay.deleteLater()
                except Exception:
                    pass
                self._confirm_overlay = None

            overlay = ConfirmationOverlay(title, detail, parent=self)
            overlay.answered.connect(self._on_confirm_answered)
            overlay.closed.connect(lambda: setattr(self, "_confirm_overlay", None))
            self._confirm_overlay = overlay
            self._position_confirm_overlay()
            overlay.show()
            overlay.raise_()

            try:
                from sound_manager import sound_mgr
                sound_mgr.play_sfx("telemetry_chirp")
            except Exception:
                pass

    def _apply_circuit_overlay(self, circuit_data: dict):
        if self._circuit_overlay:
            self._circuit_overlay.deleteLater()
            self._circuit_overlay = None

        from core.circuit_hud import CircuitPopupOverlay

        parent = self.centralWidget()
        overlay = CircuitPopupOverlay(circuit_data or {}, parent=parent)
        overlay.closed.connect(lambda: setattr(self, "_circuit_overlay", None))
        self._circuit_overlay = overlay
        overlay.move(max(8, (parent.width() - overlay.width()) // 2), max(8, (parent.height() - overlay.height()) // 2))
        overlay.show()
        overlay.raise_()

    def _apply_call_screening(self, payload: dict):
        payload = dict(payload or {})
        action = payload.get("action", "show")

        if action == "hide":
            if self._call_screening_dialog:
                self._call_screening_dialog.close()
            self._call_screening_dialog = None
            self._call_screening_transcript = None
            return

        if action == "transcript":
            if self._call_screening_transcript:
                speaker = str(payload.get("speaker", "Caller"))
                text = str(payload.get("text", ""))
                current = self._call_screening_transcript.text()
                self._call_screening_transcript.setText((current + "\n" if current else "") + f"{speaker}: {text}")
            return

        if self._call_screening_dialog:
            self._call_screening_dialog.close()

        dialog = QDialog(self)
        dialog.setWindowTitle("Brahma Evo Call Screening")
        dialog.setModal(False)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        caller = str(payload.get("caller", "Caller"))
        app_name = str(payload.get("app", "Phone / Call"))
        status = str(payload.get("status", "Screening call"))
        title = QLabel(f"{caller}  ·  {app_name}")
        title.setWordWrap(True)
        detail = QLabel(status)
        transcript = QLabel("")
        transcript.setWordWrap(True)
        transcript.setMinimumHeight(70)
        layout.addWidget(title)
        layout.addWidget(detail)
        layout.addWidget(transcript)

        buttons = QHBoxLayout()
        take_over = QPushButton("Take Over")
        hang_up = QPushButton("Hang Up")
        buttons.addWidget(take_over)
        buttons.addWidget(hang_up)
        layout.addLayout(buttons)
        take_over.clicked.connect(self._take_over_screened_call)
        hang_up.clicked.connect(self._hang_up_screened_call)

        self._call_screening_dialog = dialog
        self._call_screening_transcript = transcript
        dialog.finished.connect(lambda: setattr(self, "_call_screening_dialog", None))
        dialog.show()
        dialog.raise_()

    def _take_over_screened_call(self):
        from actions.call_assistant import take_over_active_call
        take_over_active_call()

    def _hang_up_screened_call(self):
        from actions.call_assistant import hang_up_active_call
        hang_up_active_call()

    def _on_confirm_answered(self, accepted: bool):
        try:
            from core import confirm as confirm_gate
            confirm_gate.resolve(accepted)
        except Exception:
            pass
        self._apply_confirm_overlay("hide", "", "")

    def _position_confirm_overlay(self):
        if not getattr(self, "_confirm_overlay", None):
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._confirm_overlay.width()) // 2)
        y = max(16, (geo.height() - self._confirm_overlay.height()) // 2)
        self._confirm_overlay.move(x, y)

    def show_memory_inspector(self):
        self._memory_overlay_sig.emit("show")

    def hide_memory_inspector(self):
        self._memory_overlay_sig.emit("hide")

    def _apply_memory_overlay(self, action: str):
        if action == "hide":
            if getattr(self, "_memory_overlay", None):
                try:
                    self._memory_overlay.deleteLater()
                except Exception:
                    pass
                self._memory_overlay = None
            return

        if action == "show":
            if getattr(self, "_memory_overlay", None):
                try:
                    self._memory_overlay.deleteLater()
                except Exception:
                    pass
                self._memory_overlay = None

            overlay = MemoryInspectorOverlay(parent=self)
            overlay.closed.connect(lambda: setattr(self, "_memory_overlay", None))
            self._memory_overlay = overlay
            self._position_memory_overlay()
            overlay.show()
            overlay.raise_()

    def _position_memory_overlay(self):
        if not getattr(self, "_memory_overlay", None):
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._memory_overlay.width()) // 2)
        y = max(16, (geo.height() - self._memory_overlay.height()) // 2)
        self._memory_overlay.move(x, y)

    def show_app(self):
        try:
            self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        except Exception:
            pass
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_remote_connect(self):
        if not self.on_remote_clicked:
            self._log_sig.emit("ERR: Mobile remote is not ready yet.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log_sig.emit("ERR: Mobile remote could not start. Install dashboard dependencies and try again.")
            return
        url = result[0]
        key = result[1]
        auto = result[2] if len(result) >= 3 else url
        manual = result[3] if len(result) >= 4 else url

        if self._remote_overlay is not None:
            try:
                self._remote_overlay.deleteLater()
            except Exception:
                pass
            self._remote_overlay = None

        overlay = RemoteKeyOverlay(url, key, auto, manual, parent=self)
        overlay.set_new_key_callback(self.on_remote_clicked)
        overlay.closed.connect(lambda: setattr(self, "_remote_overlay", None))
        self._remote_overlay = overlay
        self._position_remote_overlay()
        overlay.show()
        overlay.raise_()
        self._log_sig.emit(f"SYS: Mobile Connect ready at {manual}. Scan the QR code or enter key {key}.")

    def _position_remote_overlay(self):
        if not self._remote_overlay:
            return
        geo = self.geometry()
        x = max(16, (geo.width() - self._remote_overlay.width()) // 2)
        y = max(16, (geo.height() - self._remote_overlay.height()) // 2)
        self._remote_overlay.move(x, y)

    def notify_phone_connected(self):
        if self._remote_overlay is not None:
            self._remote_overlay.mark_connected()
        self._log_sig.emit("SYS: Phone connected to Brahma Evo remote.")

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

    def _attach_avatar_renderer(self):
        """Give core.avatar_runtime somewhere to send an emotion tag.

        The registry never imports a widget - that would invert the dependency
        for one renderer - so the UI offers a sink and the core fills it. Weak
        reference, because the registry outliving the window must not keep the
        window (and its WebGL context) alive.
        """
        try:
            import weakref
            from core import avatar_runtime

            ref = weakref.ref(self)

            def _sink(tag: str, intensity: float) -> bool:
                win = ref()
                if win is None:
                    return False
                bg = getattr(win, "_bg_widget", None)
                fn = getattr(bg, "set_avatar_emotion", None)
                if not callable(fn):
                    return False
                return bool(fn(tag, intensity))

            avatar_runtime.set_webengine_sink(_sink)
        except Exception:
            pass

    def refresh_avatar_renderer(self) -> bool:
        """Re-point the background at whichever avatar document is now selected.

        Called after the backend picker changes, because the two backends are
        separate documents rather than one page with a mode flag.
        """
        try:
            bg = getattr(self, "_bg_widget", None)
            fn = getattr(bg, "refresh_avatar_page", None)
            if not callable(fn):
                return False
            return bool(fn())
        except Exception:
            return False

    def _on_audio_level_sig(self, level: float):
        if hasattr(self, "_bg_widget") and hasattr(self._bg_widget, "set_audio_level"):
            self._bg_widget.set_audio_level(level)
        if hasattr(self, "_chat_workspace") and hasattr(self._chat_workspace, "set_audio_level"):
            self._chat_workspace.set_audio_level(level)
        if hasattr(self, "_command_bar") and hasattr(self._command_bar, "set_audio_level"):
            self._command_bar.set_audio_level(level)

    def set_audio_level(self, level: float):
        try:
            self._audio_level_sig.emit(float(level))
        except Exception:
            pass

    def _apply_state(self, state: str):
        self._state = state
        if hasattr(self, "_bg_widget") and hasattr(self._bg_widget, "set_ai_state"):
            self._bg_widget.set_ai_state(state)
        if hasattr(self, "_chat_workspace") and hasattr(self._chat_workspace, "set_state"):
            self._chat_workspace.set_state(state)
        if hasattr(self, "_command_bar") and hasattr(self._command_bar, "set_state"):
            self._command_bar.set_state(state)
        elif hasattr(self, "centralWidget") and hasattr(self.centralWidget(), "set_ai_state"):
            self.centralWidget().set_ai_state(state)

        # Update top taskbar status badge
        if hasattr(self, "_status_badge"):
            badge_map = {
                "LISTENING":  ("◉ LISTENING",  "#00e5ff", "rgba(0, 229, 255, 0.4)",  "rgba(0, 229, 255, 0.08)"),
                "SPEAKING":   ("◈ SPEAKING",   "#00e5ff", "rgba(0, 229, 255, 0.5)",  "rgba(0, 229, 255, 0.12)"),
                "THINKING":   ("◒ THINKING",   "#ff9100", "rgba(255, 145, 0, 0.45)", "rgba(255, 145, 0, 0.08)"),
                "PROCESSING": ("◒ PROCESSING", "#ff9100", "rgba(255, 145, 0, 0.45)", "rgba(255, 145, 0, 0.08)"),
                "EXECUTING":  ("⚡ EXECUTING",  "#7c4dff", "rgba(124, 77, 255, 0.45)","rgba(124, 77, 255, 0.08)"),
                "WORKING":    ("⚡ WORKING",    "#7c4dff", "rgba(124, 77, 255, 0.45)","rgba(124, 77, 255, 0.08)"),
                "MUTED":      ("⊗ MUTED",      "#ff3b30", "rgba(255, 59, 48, 0.45)", "rgba(255, 59, 48, 0.08)"),
                "SCANNING":   ("◓ SCANNING",   "#37ff5f", "rgba(55, 255, 95, 0.45)", "rgba(55, 255, 95, 0.08)"),
                "STANDBY":    ("◌ STANDBY",    "#6e7a8a", "rgba(110, 122, 138, 0.40)","rgba(110, 122, 138, 0.08)"),
            }
            lbl_txt, col, border, bg = badge_map.get(state, ("● ONLINE", "#00e5ff", "rgba(0, 229, 255, 0.25)", "rgba(12,14,18,220)"))
            self._status_badge.setText(lbl_txt)
            self._status_badge.setStyleSheet(
                f"color: {col}; background: {bg}; border: 1px solid {border}; border-radius: 8px; padding: 5px 12px;"
            )

        # Update command row input placeholder
        if hasattr(self, "_input"):
            ph_map = {
                "LISTENING": "Listening... (or type your command)",
                "SPEAKING": "Brahma Evo is responding...",
                "THINKING": "Brahma is thinking...",
                "PROCESSING": "Processing request...",
                "EXECUTING": "Executing action...",
                "WORKING": "Working on it...",
                "MUTED": "Microphone muted — type command here...",
                "SCANNING": "Scanning display...",
                "STANDBY": "Standby - say \"Brahma Evo\" or press Connect...",
            }
            self._input.setPlaceholderText(ph_map.get(state, "Ask Brahma Evo anything..."))

        # Update chat workspace footer status
        if hasattr(self, "_inline_workspace") and hasattr(self._inline_workspace, "_footer_status"):
            foot_map = {
                "LISTENING": "● Listening for voice command...",
                "SPEAKING": "● Brahma is speaking...",
                "THINKING": "● Thinking...",
                "PROCESSING": "● Processing...",
                "EXECUTING": "● Executing system command...",
                "WORKING": "● Working on task...",
                "MUTED": "● Voice input muted",
                "SCANNING": "● Vision system active",
                "STANDBY": "● Standby - no session (say \"Brahma Evo\" or press Connect)",
            }
            self._inline_workspace._footer_status.setText(foot_map.get(state, "Brahma Evo is online"))

        if hasattr(self, "_status_chip"):
            chip_text = {
                "THINKING": "● WORKING",
                "SPEAKING": "● SPEAKING",
                "MUTED":    "● MUTED",
                "STANDBY": "● STANDBY",
            }.get(state, "● ONLINE")
            self._status_chip.setText(chip_text)
            color = C.PRI if state == "MUTED" else "#6e7a8a" if state == "STANDBY" else C.GREEN if state in ("LISTENING", "SPEAKING") else C.WHITE
            border = C.PRI if state in ("MUTED", "THINKING") else "rgba(255,255,255,0.12)"
            self._status_chip.setStyleSheet(
                f"color: {color}; background: rgba(11,12,16,238); border: 1px solid {border}; border-radius: 999px; padding: 7px 14px;"
            )
        if hasattr(self, "_time_status_lbl"):
            status_text = {
                "THINKING": "Working",
                "SPEAKING": "Speaking",
                "MUTED": "Muted",
                "STANDBY": "On Call",
            }.get(state, "Online")
            self._time_status_lbl.setText(status_text)
            self._time_status_lbl.setStyleSheet(
                f"color: {C.GREEN if state in ('LISTENING', 'SPEAKING') else C.RED if state == 'MUTED' else '#6e7a8a' if state == 'STANDBY' else C.WHITE}; background: transparent;"
            )
        if hasattr(self, "_task_card"):
            if state in ("THINKING", "PROCESSING", "EXECUTING", "WORKING"):
                self._task_card.set_task("Working on it...", "Brahma Evo is processing your request.", 72)
            elif state == "SPEAKING":
                self._task_card.set_task("Responding...", "Brahma Evo is speaking now.", 100)
            elif state == "MUTED":
                self._task_card.set_task("Microphone muted", "Voice input is paused.", 0)
            elif state == "STANDBY":
                self._task_card.set_task(
                    "On Call standby",
                    'Socket closed. Say "Brahma Evo" or press Connect.', 0
                )
            else:
                self._task_card.set_task("Ready", "Brahma Evo is idle and ready.", 0)
        if hasattr(self, "_result_card"):
            if state in ("THINKING", "PROCESSING", "EXECUTING", "WORKING"):
                self._result_card.set_body("Action pending")
            elif state == "SPEAKING":
                self._result_card.set_body("Speaking now")
            elif state == "MUTED":
                self._result_card.set_body("Voice muted")
            elif state == "STANDBY":
                self._result_card.set_body("Waiting for wake word")
            else:
                self._result_card.set_body("Action completed")

    def set_voice_status(self, backend: str = "", live: bool | None = None,
                          detail: str = ""):
        """Report which voice backend is driving the session, and whether it is up.

        The chat/footer indicator only ever reflected the microphone, so a local
        voice session, a Gemini Live socket and a custom endpoint looked
        identical. Connectivity is now stated explicitly and is independent of
        which backend is selected.
        """
        for holder_name in ("_chat_workspace", "_command_bar"):
            holder = getattr(self, holder_name, None)
            if holder is not None and hasattr(holder, "set_voice_status"):
                try:
                    holder.set_voice_status(backend, live, detail)
                except Exception:
                    pass
        # Keep a copy on the window so any later indicator can read it too.
        if backend:
            self._voice_backend = str(backend).strip()
        if live is not None:
            self._voice_live = bool(live)
        if detail:
            self._voice_detail = str(detail).strip()
    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return (bool(d.get("gemini_api_key")) and
                    bool(d.get("os_system")))
        except Exception:
            return False

    def _apply_scan_state(self, enabled: bool, text: str = ""):
        if enabled:
            if self._scan_overlay is None:
                self._scan_overlay = ScanningOverlay()
            self._scan_overlay.show_fullscreen(text or "SCANNING SCREEN", "Analyzing display...")
        else:
            if self._scan_overlay is not None:
                self._scan_overlay.hide_overlay()

    def set_scanning(self, enabled: bool, text: str = ""):
        self._scan_sig.emit(bool(enabled), text or "")

    def set_meeting_mode(self, enabled: bool, title: str = "", summary: str = "", answer: str = "", speech: str = ""):
        self._meeting_sig.emit({
            "enabled": bool(enabled),
            "title": title or "",
            "summary": summary or "",
            "answer": answer or "",
            "speech": speech or "",
        })

    def _on_screen_capture_requested(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QBuffer, QIODevice, Qt
        try:
            screen = QApplication.primaryScreen()
            if not screen:
                self._screen_capture_result = b""
            else:
                pixmap = screen.grabWindow(0)
                pixmap = pixmap.scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                pixmap.save(buffer, 'JPG', 55)
                self._screen_capture_result = buffer.data().data()
        except Exception:
            self._screen_capture_result = b""
        finally:
            self._screen_capture_event.set()

    def capture_screen_threadsafe(self) -> bytes:
        print("[UI] capture_screen_threadsafe started")
        self._screen_capture_event.clear()
        self._screen_capture_sig.emit()
        self._screen_capture_event.wait(timeout=5.0)
        print(f"[UI] capture_screen_threadsafe finished, result {len(self._screen_capture_result) if self._screen_capture_result is not None else 'None'} bytes")
        return self._screen_capture_result

    def _apply_meeting_state(self, event: object):
        data = event if isinstance(event, dict) else {}
        enabled = bool(data.get("enabled"))
        title = (data.get("title") or "").strip()
        summary = (data.get("summary") or "").strip()
        answer = (data.get("answer") or "").strip()
        speech = (data.get("speech") or "").strip()

        if enabled:
            if self._meeting_overlay is None:
                self._meeting_overlay = MeetingOverlay()
                self._meeting_overlay.stop_requested.connect(self._request_stop_meeting)
                self._meeting_overlay.minimize_requested.connect(self._toggle_meeting_overlay)
                self._meeting_overlay.close_requested.connect(self._request_stop_meeting)
            self._meeting_overlay.set_content(
                title or "Meeting mode",
                summary or "Watching the meeting screen.",
                answer or "No question detected yet.",
                True,
                speech,
            )
            self._position_meeting_overlay()
            self._meeting_overlay.set_collapsed(self._meeting_overlay_collapsed)
            self._meeting_overlay.show()

    def _apply_task_workspace(self, event: object):
        data = event if isinstance(event, dict) else {}
        target = getattr(self, "_task_card", None)
        if target is None:
            return
        action = (data.get("action") or "update").strip().lower()
        if action == "start":
            target.start_workspace(
                data.get("command") or "",
                data.get("plan") or [],
                data.get("source") or "local",
            )
        elif action == "update":
            target.update_workspace(
                title=data.get("title"),
                command=data.get("command"),
                plan=data.get("plan"),
                status=data.get("status"),
                output=data.get("output"),
                percent=data.get("percent"),
                footer=data.get("footer"),
            )
        elif action == "finish":
            target.finish_workspace(
                data.get("result") or data.get("output") or "Done.",
                status=data.get("status") or "Task completed.",
                percent=int(data.get("percent") or 100),
            )
        elif action == "clear":
            target.clear_workspace()

    def _on_discord_status_update(self, message: str):
        if not message:
            return
        if hasattr(self, "_discord_status_lbl"):
            note = message.strip()
            self._refresh_discord_card(note)
            if self._meeting_overlay is not None:
                try:
                    self._meeting_overlay.raise_()
                except Exception:
                    pass
        else:
            if self._meeting_overlay is not None:
                self._meeting_overlay.hide()
            self._meeting_overlay_collapsed = False

    def _request_stop_meeting(self):
        if self.on_attention_action:
            try:
                self.on_attention_action({"kind": "meeting", "app": "Meeting mode"}, "stop")
            except Exception:
                pass

    def _toggle_meeting_overlay(self):
        if self._meeting_overlay is None:
            return
        self._meeting_overlay_collapsed = not self._meeting_overlay_collapsed
        self._meeting_overlay.set_collapsed(self._meeting_overlay_collapsed)
        self._position_meeting_overlay()
        if not self._meeting_overlay.isVisible():
            self._meeting_overlay.show()
        self._meeting_overlay.raise_()

    def _position_meeting_overlay(self):
        if self._meeting_overlay is None:
            return
        screen = QApplication.primaryScreen().availableGeometry()
        margin = 12
        h = self._meeting_overlay.height()
        w = min(980, max(780, screen.width() - margin * 2))
        x = screen.left() + (screen.width() - w) // 2
        y = screen.top() + margin
        self._meeting_overlay.setGeometry(x, y, w, h)

    def _show_attention_alert(self, event: object):
        data = event if isinstance(event, dict) else {}
        if self._incoming_alert is not None:
            try:
                self._incoming_alert.close()
            except Exception:
                pass
            self._incoming_alert = None

        dlg = IncomingAlertDialog(data, None)
        dlg.decision.connect(lambda decision, ev=data: self._attention_choice(ev, decision))

        if (data.get("kind") or "").strip().lower() == "call":
            self.show_app()

        screen = QApplication.primaryScreen().availableGeometry()
        margin = 16
        dlg.adjustSize()
        x = screen.right() - dlg.width() - margin
        y = screen.top() + margin
        dlg.move(x, y)
        dlg.show()
        dlg.raise_()
        self._incoming_alert = dlg

    def _attention_choice(self, event: dict, decision: str):
        if self.on_attention_action:
            try:
                self.on_attention_action(event, decision)
            except Exception:
                pass
        self._incoming_alert = None

    def _show_setup(self, defaults: dict | None = None):
        try:
            if hasattr(self, '_floating_gesture_card'):
                self._floating_gesture_card.hide()
            if self._overlay:
                self._overlay.hide()
                self._overlay.deleteLater()
                self._overlay = None
            
            # Maximize the main window for the cinematic experience
            self.showMaximized()
            
            ov = SetupOverlay(self.centralWidget(), defaults=defaults or self._load_api_defaults())
            cw = self.centralWidget()
            ov.setGeometry(0, 0, cw.width(), cw.height())
            ov.done.connect(self._on_setup_done)
            ov.show()
            ov.raise_()
            ov.activateWindow()
            self._overlay = ov
        except Exception as e:
            import traceback
            with open('setup_crash.log', 'w') as err_f:
                err_f.write(traceback.format_exc())
            raise


    # Change signature:
    def _on_setup_done(self, key: str, or_key: str, os_name: str):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            existing = self._load_api_defaults()
            API_FILE.write_text(
                json.dumps({
                    "gemini_api_key":    key,
                    "openrouter_api_key": or_key,
                    "anthropic_api_key": existing.get("anthropic_api_key", ""),
                    "os_system":         os_name,
                }, indent=4),
                encoding="utf-8",
            )
            self._ready = True
            self._api_ready = True
            if self._overlay:
                self._overlay.hide()
                self._overlay.deleteLater()
                self._overlay = None
            if hasattr(self, '_floating_gesture_card'):
                self._floating_gesture_card.show()
            self.showNormal()
            self._apply_state("LISTENING")
            self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. Brahma Evo online.")
        except Exception as e:
            self._log.append_log(f"ERR: setup failed: {e}")
            traceback.print_exc()

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
            f"QWidget#ModularRightSidebar {{ "
            f"  background: rgba(8, 10, 15, 0.22); "
            f"  border-left: 1px solid rgba(0, 229, 255, 0.25); "
            f"}}"
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
            f"""
            QFrame {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(0, 229, 255, 0.20);
                border-radius: 35px;
            }}
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


class BrahmaUI:
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

    def _load_app_settings(self) -> dict:
        if self._app_settings_cache is not None:
            return dict(self._app_settings_cache)
        settings = _default_app_settings()
        if APP_SETTINGS_FILE.exists():
            try:
                data = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        settings[k] = v
            except Exception:
                pass
        self._app_settings_cache = dict(settings)
        self._app_settings_cache_stamp = self._settings_file_stamp()
        return dict(settings)

    def _settings_file_stamp(self):
        """See MainWindow._settings_file_stamp."""
        try:
            if APP_SETTINGS_FILE.exists():
                st = APP_SETTINGS_FILE.stat()
                return (st.st_mtime_ns, st.st_size)
        except Exception:
            pass
        return None

    def _invalidate_app_settings_cache(self):
        """See MainWindow._invalidate_app_settings_cache: a second settings owner
        (core/provider_router.py, core/instruction_injector.py) writes the same
        file without touching this cache, so a stale snapshot must never be
        written back."""
        self._app_settings_cache = None

    def _save_app_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # Merge over the live file rather than trusting the caller's snapshot,
        # so a key another component changed after this dict was read survives.
        merged = dict(settings)
        if APP_SETTINGS_FILE.exists():
            try:
                on_disk = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(on_disk, dict):
                    merged = on_disk
                    merged.update(settings)
            except Exception:
                pass
        atomic_write_json(APP_SETTINGS_FILE, merged)
        self._app_settings_cache = dict(merged)

    def _save_launcher_position(self, x: int, y: int):
        try:
            settings = self._load_app_settings()
            settings["launcher_pos"] = [int(x), int(y)]
            self._save_app_settings(settings)
        except Exception:
            pass

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

    def _load_discord_settings(self) -> dict:
        settings = _default_discord_settings()
        if DISCORD_SETTINGS_FILE.exists():
            try:
                data = json.loads(DISCORD_SETTINGS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:
                pass
        if (settings.get("bot_token") or "").strip():
            settings["enabled"] = True
        return dict(settings)

    def _save_discord_settings(self, settings: dict):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        DISCORD_SETTINGS_FILE.write_text(json.dumps(settings, indent=4), encoding="utf-8")

    def _emit_discord_settings(self):
        if not hasattr(self, "_discord_token_input"):
            return
        settings = {
            "bot_token": self._discord_token_input.text().strip(),
            "enabled": bool(getattr(self, "_discord_enabled", False)),
            "channel_id": self._discord_channel_input.text().strip() if hasattr(self, "_discord_channel_input") else "",
        }
        self._save_discord_settings(settings)
        self.discord_config_changed.emit(dict(settings))
        self._refresh_discord_card()

    def _refresh_discord_card(self, note: str = ""):
        if not hasattr(self, "_discord_status_lbl"):
            return
        token = self._discord_token_input.text().strip() if hasattr(self, "_discord_token_input") else ""
        enabled = bool(getattr(self, "_discord_enabled", False))
        if note:
            status = note
            color = C.PRI if "error" in note.lower() or "missing" in note.lower() else C.TEXT_MED
        elif not token:
            status = "Token required"
            color = C.PRI
        elif enabled:
            status = "Bot enabled"
            color = C.GREEN
        else:
            status = "Bot disabled"
            color = C.TEXT_MED
        self._discord_status_lbl.setText(status)
        self._discord_status_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        if hasattr(self, "_discord_start_btn"):
            self._discord_start_btn.setEnabled(True)
        if hasattr(self, "_discord_stop_btn"):
            self._discord_stop_btn.setEnabled(True)
        if hasattr(self, "_discord_save_btn"):
            self._discord_save_btn.setText("Save Settings")

    def _startup_animation_enabled(self) -> bool:
        if platform.system() != "Windows":
            return False
        return bool(self._load_app_settings().get("startup_animation_enabled", True))

    def _set_startup_animation_enabled(self, enabled: bool) -> bool:
        try:
            settings = self._load_app_settings()
            settings["startup_animation_enabled"] = bool(enabled)
            settings["last_boot_stamp"] = _current_boot_stamp()
            self._save_app_settings(settings)
            return True
        except Exception as e:
            self._log.append_log(f"ERR: startup animation setting failed: {e}")
            return False

    def _refresh_startup_animation_button(self):
        if not hasattr(self, "_startup_anim_btn"):
            return
        if platform.system() != "Windows":
            self._startup_anim_btn.setText("Startup Animation (Windows only)")
            self._startup_anim_btn.setEnabled(False)
            return
        if self._startup_animation_enabled():
            self._startup_anim_btn.setText("Disable Startup Animation")
        else:
            self._startup_anim_btn.setText("Enable Startup Animation")

    def _toggle_startup_animation(self):
        if platform.system() != "Windows":
            return
        enabled = not self._startup_animation_enabled()
        if self._set_startup_animation_enabled(enabled):
            self._refresh_startup_animation_button()
            state = "enabled" if enabled else "disabled"
            self._log.append_log(f"SYS: Startup animation {state}.")

    def _should_play_boot_sequence(self) -> bool:
        return True

    def _mark_boot_sequence_played(self):
        try:
            settings = self._load_app_settings()
            settings["last_boot_stamp"] = _current_boot_stamp()
            settings["boot_sequence_played"] = True
            self._save_app_settings(settings)
        except Exception:
            pass

    def play_boot_sequence(self, finished_callback=None):
        self._mark_boot_sequence_played()
        if self._boot_overlay is None:
            self._boot_overlay = BootSequenceOverlay()
            def _on_boot_finished():
                try:
                    self._win.setWindowState(
                        (self._win.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
                    )
                    self._win.showNormal()
                    self._win.raise_()
                    self._win.activateWindow()
                    self._launcher.hide()
                except Exception:
                    try:
                        self._win.show()
                    except Exception:
                        pass
                if finished_callback:
                    try:
                        finished_callback()
                    except Exception:
                        pass
            self._boot_overlay.finished.connect(_on_boot_finished)
        self._boot_overlay.start()

    # Thread-safe helpers for driving the boot overlay from background threads
    def boot_add_step(self, text: str):
        try:
            if not self._boot_overlay:
                return None
            QTimer.singleShot(0, lambda: self._boot_overlay.add_step(text))
        except Exception:
            pass

    def boot_set_step_status(self, text: str, status: str):
        try:
            if not self._boot_overlay:
                return
            QTimer.singleShot(0, lambda: self._boot_overlay.set_step_status(text, status))
        except Exception:
            pass

    def boot_set_progress(self, percent: int, tip: str | None = None):
        try:
            if not self._boot_overlay:
                return
            QTimer.singleShot(0, lambda: self._boot_overlay.set_progress(percent, tip))
        except Exception:
            pass

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
        restart_action = menu.addAction("Restart")
        quit_action = menu.addAction("Quit")

        open_app_action.triggered.connect(self.show_main)
        open_action.triggered.connect(self._show_workspace_sidebar)
        close_action.triggered.connect(self._close_workspace_sidebar)
        startup_action.triggered.connect(lambda: self._toggle_workspace_on_startup(startup_action.isChecked()))
        show_icon_action.triggered.connect(self._show_floating_icon)
        hide_icon_action.triggered.connect(self._hide_launcher_with_protection)
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

    def _toggle_workspace_on_startup(self, enabled: bool):
        try:
            settings = self._load_app_settings()
            settings["show_workspace_on_startup"] = bool(enabled)
            self._save_app_settings(settings)
        except Exception:
            pass

    def _hide_launcher_with_protection(self):
        self._show_hide_launcher_confirm()

    def _show_hide_launcher_confirm(self):
        panel = LauncherControlPanel(
            startup_workspace=bool(self._load_app_settings().get("show_workspace_on_startup", False)),
            on_open=self._show_workspace_sidebar,
            on_close=self._close_workspace_sidebar,
            on_toggle_startup=self._toggle_workspace_on_startup,
            on_hide_icon=self._launcher.hide,
            on_restart=self._restart_app,
            on_quit=self._app.quit,
            on_open_app=self.show_main,
            on_show_icon=self._show_floating_icon,
            on_open_dev=self._open_developer_mode_dialog,
            on_setting_toggle=self._on_panel_setting_toggle,
        )
        self._control_panel = panel
        self._position_control_panel(panel)
        panel.show()
        panel.raise_()
        panel.activateWindow()

    def _position_control_panel(self, panel: LauncherControlPanel):
        try:
            geo = self._launcher.geometry()
            panel.adjustSize()
            panel.move(max(20, geo.left() - panel.width() - 16), max(20, geo.top() - 10))
        except Exception:
            screen = QApplication.primaryScreen().availableGeometry()
            panel.move(screen.center().x() - panel.width() // 2, screen.center().y() - panel.height() // 2)

    def _show_control_panel(self):
        self._show_hide_launcher_confirm()

    def _handle_launcher_action(self, action: str):
        action = (action or "").strip().lower()
        if action == "open_app":
            self.show_main()
        elif action == "open_workspace":
            self._show_workspace_sidebar()
        elif action == "close_workspace":
            self._close_workspace_sidebar()
        elif action == "show_icon":
            self._show_floating_icon()
        elif action == "hide_icon":
            self._hide_launcher_with_protection()
        elif action == "restart":
            self._restart_app()
        elif action == "quit":
            self._app.quit()
        elif action == "toggle_startup":
            current = bool(self._load_app_settings().get("show_workspace_on_startup", False))
            self._toggle_workspace_on_startup(not current)

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



