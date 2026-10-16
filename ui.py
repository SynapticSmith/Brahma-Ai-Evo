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


class RemoteKeyOverlay(QWidget):
    closed = pyqtSignal()

    def __init__(self, url: str, key: str, auto: str, manual: str, parent=None):
        super().__init__(parent)
        self._on_new_key = None
        self._manual_url = manual or url
        self._auto_login_url = auto or url
        self._expiry = time.time() + 600

        # modern glassmorphism panel
        frame = QFrame(self)
        frame.setObjectName("RemoteOverlayMainFrame")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(32, 32, 32, 32)
        lay.setSpacing(16)
        
        try:
            self.setFixedSize(560, 680)
            frame.setFixedSize(self.size())
        except Exception:
            self.setFixedSize(520, 640)
            frame.setFixedSize(self.size())
            
        frame.setStyleSheet(f"""
            QFrame#RemoteOverlayMainFrame {{
                background: rgba(10, 12, 18, 250);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 24px;
            }}
        """)
        
        # elegant shadow
        try:
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(60)
            glow.setColor(QColor(0, 0, 0, 180))
            glow.setOffset(0, 12)
            frame.setGraphicsEffect(glow)
        except Exception:
            pass

        title = QLabel("Mobile Connect")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        lay.addWidget(title)

        subtitle = QLabel("Scan the QR code with your phone to remotely control Brahma Evo.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Segoe UI", 9))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; border: none; margin-bottom: 10px;")
        lay.addWidget(subtitle)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(240, 240)
        self._qr_label.setStyleSheet("background: white; border-radius: 12px; padding: 12px; border: none;")
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        manual_hint = QLabel("Manual address")
        manual_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        manual_hint.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        manual_hint.setStyleSheet(f"color: {C.TEXT_DIM}; border: none; margin-top: 10px;")
        lay.addWidget(manual_hint)

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._url_lbl.setFont(QFont("Consolas", 10))
        self._url_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 6px;")
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_lbl.setFont(QFont("Consolas", 36, QFont.Weight.Black))
        self._key_lbl.setStyleSheet(f"""
            color: #00e5ff;
            background: rgba(0, 229, 255, 0.05);
            border: 1px solid rgba(0, 229, 255, 0.2);
            border-radius: 16px;
            padding: 18px;
            letter-spacing: 14px;
            font-weight: 900;
            margin-top: 12px;
            margin-bottom: 8px;
        """)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel("")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer_lbl.setFont(QFont("Segoe UI", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; border: none;")
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(14)
        
        self._new_btn = QPushButton("New Key")
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.setFixedHeight(40)
        self._new_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._new_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }}
            QPushButton:hover {{ 
                background: rgba(0, 229, 255, 0.08); 
                border: 1px solid rgba(0, 229, 255, 0.3);
                color: #00e5ff;
            }}
        """)
        self._new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(self._new_btn)

        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFixedHeight(40)
        close_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }}
            QPushButton:hover {{ 
                background: rgba(255, 60, 60, 0.08); 
                border: 1px solid rgba(255, 60, 60, 0.3);
                color: #ff6b6b;
            }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._update_qr(self._auto_login_url)
        self._tick()

        self.adjustSize()
        try:
            self.setFixedSize(max(360, self.width()), max(360, self.height()))
        except Exception:
            self.setFixedSize(420, 520)

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("NO URL")
            return
        try:
            import qrcode
            from io import BytesIO
            qr = qrcode.QRCode(box_size=5, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            pix = QPixmap()
            pix.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(pix.scaled(172, 172, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except ImportError:
            self._qr_label.setText("Install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self._qr_label.setStyleSheet("color: #111; background: white; border-radius: 12px; padding: 6px;")
        except Exception:
            self._qr_label.setText("QR failed")

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        mins, secs = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in {mins:02d}:{secs:02d}")
        if remaining <= 0:
            self._do_close()

    def mark_connected(self) -> None:
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(55,255,95,20);
            border: 1px solid rgba(55,255,95,150);
            border-radius: 10px;
            padding: 8px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("OK")
        self._qr_label.setFont(QFont("Segoe UI", 34, QFont.Weight.Black))
        self._qr_label.setStyleSheet("color: #37ff5f; background: #041006; border-radius: 12px;")
        self._timer_lbl.setText("Phone connected. Brahma Evo remote is ready.")

    def _refresh_key(self):
        if not self._on_new_key:
            return
        result = self._on_new_key()
        if not result:
            return
        url = result[0]
        key = result[1]
        auto = result[2] if len(result) >= 3 else url
        manual = result[3] if len(result) >= 4 else url
        self._manual_url = manual or url
        self._auto_login_url = auto or url
        self._url_lbl.setText(self._manual_url)
        self._key_lbl.setText(key)
        self._key_lbl.setStyleSheet(f"""
            color: {C.WHITE};
            background: rgba(0, 229, 255,28);
            border: 1px solid {C.PRI};
            border-radius: 10px;
            padding: 8px;
            letter-spacing: 9px;
        """)
        self._update_qr(self._auto_login_url)
        self._expiry = time.time() + 600
        self._ctimer.start(1000)
        self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class DailyBriefingOverlay(QWidget):
    closed = pyqtSignal()

    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        import datetime

        if isinstance(data, str):
            self._data = {
                "greeting": "Daily Briefing",
                "timestamp": datetime.datetime.now().strftime("%A, %B %d • %I:%M %p"),
                "spoken_narrative": data,
                "weather": {"status": "info", "temp_c": "--", "condition": "Atmospheric data", "city": "Local"},
                "calendar": {"count": 0, "events": []},
                "gmail": {"count": 0, "senders": []},
                "instagram": {"count": 0, "senders": []},
                "headlines": [],
            }
        elif isinstance(data, dict):
            self._data = data
        else:
            self._data = {}

        self._remaining_secs = 30
        self.setFixedSize(720, 540)

        # Glassmorphism container
        self._frame = QFrame(self)
        self._frame.setObjectName("BriefingOverlayMainFrame")
        self._frame.setFixedSize(self.size())
        self._frame.setStyleSheet(f"""
            QFrame#BriefingOverlayMainFrame {{
                background: rgba(10, 12, 18, 250);
                border: 1px solid rgba(0, 229, 255, 0.4);
                border-radius: 20px;
            }}
        """)

        try:
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(50)
            glow.setColor(QColor(0, 0, 0, 220))
            glow.setOffset(0, 10)
            self._frame.setGraphicsEffect(glow)
        except Exception:
            pass

        main_lay = QVBoxLayout(self._frame)
        main_lay.setContentsMargins(24, 20, 24, 20)
        main_lay.setSpacing(14)

        # Header Row
        hdr_lay = QHBoxLayout()
        hdr_lay.setSpacing(12)

        hdr_info = QVBoxLayout()
        hdr_info.setSpacing(2)

        title_lbl = QLabel("⚡ BRAHMA INTELLIGENCE // UNIFIED MORNING BRIEFING")
        title_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1.5px; background: transparent; border: none;")
        hdr_info.addWidget(title_lbl)

        ts_str = self._data.get("timestamp") or datetime.datetime.now().strftime("%A, %B %d • %I:%M %p")
        ts_lbl = QLabel(ts_str)
        ts_lbl.setFont(QFont("Segoe UI", 9))
        ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        hdr_info.addWidget(ts_lbl)

        hdr_lay.addLayout(hdr_info)
        hdr_lay.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px;
            }}
            QPushButton:hover {{
                background: rgba(255, 60, 60, 0.2);
                border: 1px solid rgba(255, 60, 60, 0.5);
                color: #ff5555;
            }}
        """)
        close_btn.clicked.connect(self._do_close)
        hdr_lay.addWidget(close_btn)

        main_lay.addLayout(hdr_lay)

        # 2x2 Bento Grid
        grid = QGridLayout()
        grid.setSpacing(12)

        # 1. Weather Card
        grid.addWidget(self._build_weather_card(), 0, 0)
        # 2. Calendar Card
        grid.addWidget(self._build_calendar_card(), 0, 1)
        # 3. Gmail Card
        grid.addWidget(self._build_gmail_card(), 1, 0)
        # 4. Instagram Card
        grid.addWidget(self._build_instagram_card(), 1, 1)

        main_lay.addLayout(grid)

        # Spoken Narrative Box
        narrative_frame = QFrame()
        narrative_frame.setStyleSheet("""
            QFrame {
                background: rgba(0, 0, 0, 0.45);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 4px;
            }
        """)
        narr_lay = QVBoxLayout(narrative_frame)
        narr_lay.setContentsMargins(12, 10, 12, 10)
        narr_lay.setSpacing(4)

        narr_tag = QLabel("🎙 EXECUTIVE SUMMARY")
        narr_tag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        narr_tag.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1px; background: transparent; border: none;")
        narr_lay.addWidget(narr_tag)

        narrative_text = self._data.get("spoken_narrative") or "No briefing narrative generated."
        narr_lbl = QLabel(narrative_text)
        narr_lbl.setWordWrap(True)
        narr_lbl.setFont(QFont("Segoe UI", 9))
        narr_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none; line-height: 1.4;")
        narr_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        narr_lay.addWidget(narr_lbl)

        main_lay.addWidget(narrative_frame)

        # Bottom Bar: Auto-dismiss countdown + action button
        bot_lay = QHBoxLayout()
        bot_lay.setSpacing(12)

        self._countdown_lbl = QLabel(f"Auto-dismissing in {self._remaining_secs}s")
        self._countdown_lbl.setFont(QFont("Segoe UI", 8))
        self._countdown_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        bot_lay.addWidget(self._countdown_lbl)

        bot_lay.addStretch()

        dismiss_btn = QPushButton("DISMISS")
        dismiss_btn.setFixedSize(100, 30)
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        dismiss_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.12);
                color: {C.PRI};
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 8px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.25);
                border: 1px solid {C.PRI};
            }}
        """)
        dismiss_btn.clicked.connect(self._do_close)
        bot_lay.addWidget(dismiss_btn)

        main_lay.addLayout(bot_lay)

        # Auto-hide timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _create_bento_box(self) -> QFrame:
        box = QFrame()
        box.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.025);
                border: 1px solid rgba(0, 229, 255, 0.18);
                border-radius: 12px;
            }
        """)
        return box

    def _build_weather_card(self) -> QFrame:
        box = self._create_bento_box()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)

        w = self._data.get("weather") or {}
        tag = QLabel("🌤  ATMOSPHERE & WEATHER")
        tag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        tag.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1px; background: transparent; border: none;")
        lay.addWidget(tag)

        temp = w.get("temp_c")
        temp_str = f"{temp}°C" if temp is not None else "--°C"
        cond = w.get("condition") or "Clear"
        city = w.get("city") or "Local Station"

        val_row = QHBoxLayout()
        val_lbl = QLabel(temp_str)
        val_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        val_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
        val_row.addWidget(val_lbl)

        cond_lbl = QLabel(f"{cond}\n{city}")
        cond_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        cond_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        cond_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_row.addWidget(cond_lbl)
        lay.addLayout(val_row)

        hum = w.get("humidity", "--")
        wind = w.get("wind_kmph", "--")
        meta_lbl = QLabel(f"Humidity: {hum}% • Wind: {wind} km/h")
        meta_lbl.setFont(QFont("Segoe UI", 8))
        meta_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        lay.addWidget(meta_lbl)
        return box

    def _build_calendar_card(self) -> QFrame:
        box = self._create_bento_box()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)

        cal = self._data.get("calendar") or {}
        tag = QLabel("📅  TODAY'S AGENDA")
        tag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        tag.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1px; background: transparent; border: none;")
        lay.addWidget(tag)

        count = cal.get("count", 0)
        events = cal.get("events", [])

        if count > 0:
            val_lbl = QLabel(f"{count} Event{'s' if count > 1 else ''}")
            val_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            lay.addWidget(val_lbl)

            evt_summary = events[0] if len(events) == 1 else f"{events[0]} (+{len(events)-1} more)"
            sub_lbl = QLabel(evt_summary[:38] + ("..." if len(evt_summary) > 38 else ""))
            sub_lbl.setFont(QFont("Segoe UI", 8))
            sub_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            lay.addWidget(sub_lbl)
        else:
            val_lbl = QLabel("Clear Schedule")
            val_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
            lay.addWidget(val_lbl)

            sub_lbl = QLabel("No events booked today")
            sub_lbl.setFont(QFont("Segoe UI", 8))
            sub_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            lay.addWidget(sub_lbl)

        meta_lbl = QLabel("Google Calendar Active")
        meta_lbl.setFont(QFont("Segoe UI", 8))
        meta_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        lay.addWidget(meta_lbl)
        return box

    def _build_gmail_card(self) -> QFrame:
        box = self._create_bento_box()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)

        gm = self._data.get("gmail") or {}
        tag = QLabel("✉  GMAIL COMMUNICATIONS")
        tag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        tag.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1px; background: transparent; border: none;")
        lay.addWidget(tag)

        count = gm.get("count", 0)
        senders = gm.get("senders", [])

        if count > 0:
            val_lbl = QLabel(f"{count} Unread")
            val_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            lay.addWidget(val_lbl)

            if senders:
                s_str = f"From: {', '.join(senders[:2])}"
                if len(senders) > 2:
                    s_str += f" +{len(senders)-2}"
            else:
                s_str = "Unread messages awaiting review"
            sub_lbl = QLabel(s_str[:38] + ("..." if len(s_str) > 38 else ""))
            sub_lbl.setFont(QFont("Segoe UI", 8))
            sub_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            lay.addWidget(sub_lbl)
        else:
            val_lbl = QLabel("Inbox Zero")
            val_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
            lay.addWidget(val_lbl)

            sub_lbl = QLabel("All communications caught up")
            sub_lbl.setFont(QFont("Segoe UI", 8))
            sub_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            lay.addWidget(sub_lbl)

        meta_lbl = QLabel("Google Workspace Connected")
        meta_lbl.setFont(QFont("Segoe UI", 8))
        meta_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        lay.addWidget(meta_lbl)
        return box

    def _build_instagram_card(self) -> QFrame:
        box = self._create_bento_box()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)

        ig = self._data.get("instagram") or {}
        tag = QLabel("📸  INSTAGRAM INTEL")
        tag.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        tag.setStyleSheet(f"color: {C.PRI}; letter-spacing: 1px; background: transparent; border: none;")
        lay.addWidget(tag)

        count = ig.get("count", 0)
        senders = ig.get("senders", [])

        if count > 0:
            val_lbl = QLabel(f"{count} New DM{'s' if count > 1 else ''}")
            val_lbl.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            lay.addWidget(val_lbl)

            if senders:
                s_str = f"From: {', '.join(['@' + s for s in senders[:2]])}"
                if len(senders) > 2:
                    s_str += f" +{len(senders)-2}"
            else:
                s_str = "Direct messages awaiting review"
            sub_lbl = QLabel(s_str[:38] + ("..." if len(s_str) > 38 else ""))
            sub_lbl.setFont(QFont("Segoe UI", 8))
            sub_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            lay.addWidget(sub_lbl)
        else:
            val_lbl = QLabel("0 Direct Messages")
            val_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            val_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
            lay.addWidget(val_lbl)

            sub_lbl = QLabel("No pending notifications")
            sub_lbl.setFont(QFont("Segoe UI", 8))
            sub_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            lay.addWidget(sub_lbl)

        meta_lbl = QLabel("Instagram Browser Engine Ready")
        meta_lbl.setFont(QFont("Segoe UI", 8))
        meta_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        lay.addWidget(meta_lbl)
        return box

    def _tick(self):
        self._remaining_secs -= 1
        if self._remaining_secs <= 0:
            self._do_close()
        else:
            self._countdown_lbl.setText(f"Auto-dismissing in {self._remaining_secs}s")

    def _do_close(self):
        if hasattr(self, "_timer") and self._timer.isActive():
            self._timer.stop()
        self.hide()
        self.closed.emit()


class ConfirmationOverlay(QWidget):
    """Physical confirmation gate for irreversible actions (shutdown, restart, wifi toggles)."""
    answered = pyqtSignal(bool)
    closed   = pyqtSignal()
    _OW = 440

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmationOverlay {{
                background: rgba(18, 10, 4, 250);
                border: 2px solid {C.ACC};
                border-radius: 12px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        hdr = QLabel("⚠  PHYSICAL CONFIRMATION REQUIRED")
        hdr.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent; letter-spacing: 0.5px;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Segoe UI", 9))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        self._remaining_secs = 90
        self._countdown_lbl = QLabel(f"Confirmation auto-cancels in {self._remaining_secs}s")
        self._countdown_lbl.setFont(QFont("Segoe UI", 8))
        self._countdown_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(self._countdown_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)

        yes = QPushButton("CONFIRM")
        yes.setFixedHeight(36)
        yes.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.15);
                color: {C.ACC};
                border: 1.5px solid {C.ACC};
                border-radius: 6px;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.35);
                color: {C.WHITE};
            }}
        """)
        yes.clicked.connect(self._on_confirm)
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(36)
        no.setFont(QFont("Segoe UI", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                color: {C.TEXT};
                border-color: {C.BORDER_B};
                background: rgba(255, 255, 255, 0.10);
            }}
        """)
        no.clicked.connect(self._on_cancel)
        row.addWidget(no)
        lay.addLayout(row)

        no.setDefault(True)
        no.setFocus()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._remaining_secs -= 1
        if self._remaining_secs <= 0:
            self._on_cancel()
        else:
            self._countdown_lbl.setText(f"Confirmation auto-cancels in {self._remaining_secs}s")

    def _on_confirm(self):
        if hasattr(self, "_timer") and self._timer.isActive():
            self._timer.stop()
        self.hide()
        self.answered.emit(True)
        self.closed.emit()

    def _on_cancel(self):
        if hasattr(self, "_timer") and self._timer.isActive():
            self._timer.stop()
        self.hide()
        self.answered.emit(False)
        self.closed.emit()


class MemoryInspectorOverlay(QWidget):
    """HUD overlay inspecting long-term stored facts, with instant search and manual deletion."""
    closed = pyqtSignal()
    _OW = 560

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryInspectorOverlay {{
                background: rgba(8, 10, 15, 250);
                border: 1.5px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(24, 20, 24, 20)
        self._lay.setSpacing(10)
        self._filter_query = ""
        self._rebuild()

    def _rebuild(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT BRAHMA REMEMBERS")
        hdr.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 0.5px;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()
        if self._filter_query:
            q = self._filter_query.lower()
            rows = [r for r in rows if q in r.get("key", "").lower() or q in r.get("value", "").lower() or q in r.get("category", "").lower()]

        cap = QLabel(
            f"{len(rows)} stored memory entries. Stored locally in memory/long_term.json. "
            f"Brahma recalls these during relevant conversations."
        )
        cap.setWordWrap(True)
        cap.setFont(QFont("Segoe UI", 8))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        s_row = QHBoxLayout()
        search_input = QLineEdit()
        search_input.setPlaceholderText("Filter memories (e.g. coffee, name, work, python)...")
        search_input.setText(self._filter_query)
        search_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(20, 24, 32, 220);
                color: {C.WHITE};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
            }}
        """)
        search_input.textChanged.connect(self._on_search_changed)
        s_row.addWidget(search_input)
        self._lay.addLayout(s_row)

        if not rows:
            empty = QLabel("No matching memories found.")
            empty.setFont(QFont("Segoe UI", 10))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; padding: 20px 0;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(380, 42 * len(rows) + 20))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 6px; "
                f"background: rgba(12, 15, 20, 180); }}"
            )
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            ilay = QVBoxLayout(inner)
            ilay.setContentsMargins(8, 8, 8, 8)
            ilay.setSpacing(6)

            for r in rows:
                line = QHBoxLayout()
                line.setSpacing(8)

                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b>: <span style='color:{C.TEXT_MED}'>{r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Segoe UI", 9))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4].upper()} · {r.get('updated') or '—'}")
                meta.setFont(QFont("Segoe UI", 8))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(22, 22)
                rm.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this fact")
                rm.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent;
                        color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER};
                        border-radius: 4px;
                    }}
                    QPushButton:hover {{
                        color: {C.RED};
                        border-color: {C.RED};
                        background: rgba(255, 59, 48, 0.15);
                    }}
                """)
                rm.clicked.connect(lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(34)
        close.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
            }}
            QPushButton:hover {{
                color: {C.WHITE};
                border-color: {C.BORDER_B};
                background: rgba(0, 229, 255, 0.15);
            }}
        """)
        close.clicked.connect(self._do_close)
        self._lay.addWidget(close)

        self.adjustSize()
        p = self.parentWidget()
        if p is not None:
            self.move(max(16, (p.width() - self.width()) // 2), max(16, (p.height() - self.height()) // 2))

    def _on_search_changed(self, text: str):
        self._filter_query = text.strip()
        self._rebuild()

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        QTimer.singleShot(0, self._rebuild)

    def _do_close(self):
        self.hide()
        self.closed.emit()


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


def _logo_icon() -> QIcon:
    return QIcon(str(LOGO_ICO if LOGO_ICO.exists() else LOGO_FILE))


def _logo_pixmap(size: int) -> QPixmap:
    pix = QPixmap(str(LOGO_FILE))
    if pix.isNull():
        return QPixmap(size, size)
    return pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def _framed_logo(size: int, icon_size: int | None = None, *, bg: str = "rgba(18,18,18,240)",
                 border: str = None, radius: int | None = None, inset: int = 6) -> QFrame:
    border = border or C.BORDER_B
    radius = radius if radius is not None else max(10, size // 4)
    icon_size = icon_size or max(8, size - inset * 2)
    frame = QFrame()
    frame.setFixedSize(size, size)
    frame.setStyleSheet(
        f"background: {bg}; border: 1px solid {border}; border-radius: {radius}px;"
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(inset, inset, inset, inset)
    lay.setSpacing(0)
    lbl = QLabel()
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setPixmap(_logo_pixmap(icon_size))
    lbl.setStyleSheet("background: transparent; border: none;")
    lay.addWidget(lbl)
    return frame


def _icon_pixmap(kind: str, size: int = 18) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(qcol(C.WHITE), max(2.2, size * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "attach":
        # More readable paperclip shape
        p.drawArc(QRectF(size*0.22, size*0.14, size*0.42, size*0.58), 35*16, 290*16)
        p.drawArc(QRectF(size*0.42, size*0.24, size*0.28, size*0.44), 35*16, 290*16)
        p.drawLine(QPointF(size*0.28, size*0.56), QPointF(size*0.38, size*0.66))
    elif kind == "mic":
        # Clearer microphone silhouette
        p.drawRoundedRect(QRectF(size*0.31, size*0.14, size*0.38, size*0.48), size*0.16, size*0.16)
        p.drawLine(QPointF(size*0.50, size*0.62), QPointF(size*0.50, size*0.83))
        p.drawLine(QPointF(size*0.36, size*0.83), QPointF(size*0.64, size*0.83))
        p.drawLine(QPointF(size*0.42, size*0.70), QPointF(size*0.58, size*0.70))
    elif kind == "send":
        p.drawLine(QPointF(size*0.20, size*0.50), QPointF(size*0.70, size*0.50))
        p.drawLine(QPointF(size*0.48, size*0.30), QPointF(size*0.70, size*0.50))
        p.drawLine(QPointF(size*0.48, size*0.70), QPointF(size*0.70, size*0.50))

    p.end()
    return px


def _attach_pulse_glow(widget: QWidget, *, color: str = C.WHITE, blur_min: float = 12.0,
                       blur_max: float = 28.0, alpha: int = 180, period_ms: int = 2400) -> None:
    # Intentionally disabled for performance. Kept as a no-op so existing calls
    # do not need to change across the UI.
    return


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

class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = _quiet_run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = _quiet_run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = _quiet_run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS â€” powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = _quiet_run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = _quiet_run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = _quiet_run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

_CAM_OK_CACHE = {"ok": False, "ts": 0.0}



def _camera_available() -> bool:
    return True
class _GestureRenderCanvas(QWidget):
    CONNECTIONS = [
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (5, 9), (9, 10), (10, 11), (11, 12),
        (9, 13), (13, 14), (14, 15), (15, 16),
        (13, 17), (17, 18), (18, 19), (19, 20),
        (0, 17)
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)
        self._landmarks: list[tuple[float, float, float]] = []
        self._hand_visible = False
        self._search_phase = 0
        self._target_opacity = 0.0
        self._skeleton_opacity = 0.0
        self._invert_x = True

    def set_invert_x(self, invert: bool):
        self._invert_x = invert
        self.update()

    def set_landmarks(self, landmarks: list[tuple[float, float, float]]):
        self._landmarks = landmarks or []
        self.update()

    def set_hand_visible(self, visible: bool):
        self._hand_visible = visible
        self._target_opacity = 1.0 if visible else 0.0
        self.update()

    def set_search_phase(self, phase: int):
        self._search_phase = phase
        self.update()

    def _normalized_points(self, rect: QRectF) -> list[QPointF]:
        if not self._landmarks:
            return []
        xs = [p[0] for p in self._landmarks]
        ys = [p[1] for p in self._landmarks]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        bbox_w = max_x - min_x
        bbox_h = max_y - min_y
        if bbox_w < 1e-4:
            bbox_w = 1e-4
        if bbox_h < 1e-4:
            bbox_h = 1e-4
        avail_w = rect.width() * 0.82
        avail_h = rect.height() * 0.82
        scale = min(avail_w / bbox_w, avail_h / bbox_h)
        center_x = rect.center().x()
        center_y = rect.center().y()
        mid_x = (min_x + max_x) / 2.0
        mid_y = (min_y + max_y) / 2.0
        points: list[QPointF] = []
        for x, y, _ in self._landmarks:
            if getattr(self, "_invert_x", True):
                px = center_x - (x - mid_x) * scale
            else:
                px = center_x + (x - mid_x) * scale
            py = center_y + (y - mid_y) * scale
            points.append(QPointF(px, py))
        return points

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(3, 4, 7))

        if self._hand_visible and len(self._landmarks) >= 21:
            # animate opacity toward target
            self._skeleton_opacity += (self._target_opacity - self._skeleton_opacity) * 0.24
            pts = self._normalized_points(rect)
            if pts:
                # soft glow
                glow_pen = QPen(QColor(0, 229, 255, int(120 * self._skeleton_opacity)), 18,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(glow_pen)
                for a, b in self.CONNECTIONS:
                    painter.drawLine(pts[a], pts[b])

                edge_pen = QPen(QColor(255, 220, 150, int(220 * self._skeleton_opacity)), 4,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                painter.setPen(edge_pen)
                for a, b in self.CONNECTIONS:
                    painter.drawLine(pts[a], pts[b])

                for point in pts:
                    radius = 7.0
                    grad = QRadialGradient(point, radius * 2.2)
                    grad.setColorAt(0.0, QColor(255, 255, 255, int(240 * self._skeleton_opacity)))
                    grad.setColorAt(0.15, QColor(0, 229, 255, int(180 * self._skeleton_opacity)))
                    grad.setColorAt(1.0, QColor(255, 160, 40, int(16 * self._skeleton_opacity)))
                    painter.setBrush(QBrush(grad))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(point, radius * 1.4, radius * 1.4)
                    painter.setBrush(QColor(255, 255, 255, int(230 * self._skeleton_opacity)))
                    painter.drawEllipse(point, 3.5, 3.5)
        else:
            self._skeleton_opacity += (self._target_opacity - self._skeleton_opacity) * 0.24
            dot_count = (self._search_phase // 8) % 4
            message = "Searching for hand" + ("." * dot_count)
            painter.setPen(QColor(200, 200, 220, 180))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)

        painter.end()


class GestureCameraPreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GestureCameraPreview")
        self.setStyleSheet(
            f"""
            QFrame#GestureCameraPreview {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(9, 10, 14, 255),
                    stop:1 rgba(3, 4, 7, 255));
                border: 1px solid rgba(0, 229, 255, 0.24);
                border-radius: 16px;
            }}
            QLabel {{ background: transparent; }}
            """
        )
        self._cap = None
        self._timer = None
        self._hands = None
        self._use_tasks_api = False
        self._vision_module = None
        self._prev_pinch = False
        self._gesture_tracker = GestureTracker()
        self._smoothed_cursor: tuple[float, float] | None = None
        self._smoothed_screen: tuple[float, float] | None = None
        self._smoothed_landmarks: list[tuple[float, float, float]] | None = None
        self._search_phase = 0
        self._smoothing_alpha = 0.8
        self._gesture_canvas_alpha = 0.0
        self._sensitivity = 2.2
        self._sensitivity_levels = {"Low": 1.6, "Medium": 2.2, "High": 3.0, "Ultra": 4.0}
        self._invert_cursor_x = True
        self._invert_cursor_y = False
        self._cursor_calibration_x_min: float | None = None
        self._cursor_calibration_x_max: float | None = None
        self._cursor_calibration_y_min: float | None = None
        self._cursor_calibration_y_max: float | None = None
        self._last_screen_pos: tuple[int, int] | None = None
        self._cursor_anchor: tuple[float, float] | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)

        title = QLabel("HAND TRACKING")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        header_row.addWidget(title)
        header_row.addStretch(1)

        self._status_dot = QLabel()
        self._status_dot.setFixedSize(12, 12)
        self._status_dot.setStyleSheet("border-radius: 6px; background: #ffb347;")
        header_row.addWidget(self._status_dot)

        self._status_text = QLabel("SEARCHING")
        self._status_text.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._status_text.setStyleSheet("color: #ffb347;")
        header_row.addWidget(self._status_text)

        self._invert_btn = QPushButton("⇄ Invert: ON")
        self._invert_btn.setCheckable(True)
        self._invert_btn.setChecked(True)
        self._invert_btn.setFixedHeight(26)
        self._invert_btn.setStyleSheet(
            "QPushButton { background: rgba(0, 229, 255, 0.15); color: #ffb347; border: 1px solid rgba(0, 229, 255, 0.4); border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: bold; }"
            "QPushButton:!checked { background: rgba(255, 255, 255, 0.05); color: #888; border: 1px solid rgba(255, 255, 255, 0.15); }"
        )
        def _toggle_invert(checked):
            self._invert_cursor_x = checked
            self._hand_canvas.set_invert_x(checked)
            self._invert_btn.setText("⇄ Invert: ON" if checked else "⇄ Invert: OFF")
        self._invert_btn.toggled.connect(_toggle_invert)
        header_row.addWidget(self._invert_btn)

        self._sensitivity_select = QComboBox()
        self._sensitivity_select.addItems(["Low", "Medium", "High", "Ultra"])
        self._sensitivity_select.setCurrentText("Medium")
        self._sensitivity_select.setFixedWidth(84)
        self._sensitivity_select.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.05); color: #f4f6f8; border: 1px solid rgba(0, 229, 255,0.24); border-radius: 8px; padding: 4px 8px; }"
            "QComboBox::drop-down { border: none; }")
        self._sensitivity_select.currentTextChanged.connect(self._set_sensitivity_level)
        header_row.addWidget(self._sensitivity_select)
        lay.addLayout(header_row)

        self._hand_canvas = _GestureRenderCanvas(self)
        self._hand_canvas.setFixedHeight(220)
        self._hand_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(self._hand_canvas)

        self._status_hint_label = QLabel("Initializing hand detection...")
        self._status_hint_label.setFont(QFont("Segoe UI", 8))
        self._status_hint_label.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._status_hint_label.setWordWrap(True)
        lay.addWidget(self._status_hint_label)

        footer = QGridLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setHorizontalSpacing(16)
        footer.setVerticalSpacing(8)

        self._status_value = QLabel("Searching")
        self._confidence_value = QLabel("0%")
        self._gesture_value = QLabel("None")
        self._cursor_value = QLabel("Inactive")

        for idx, (label_text, value_label) in enumerate([
            ("Status", self._status_value),
            ("Confidence", self._confidence_value),
            ("Gesture", self._gesture_value),
            ("Cursor", self._cursor_value),
        ]):
            label = QLabel(label_text.upper())
            label.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
            label.setStyleSheet(f"color: {C.TEXT_DIM};")
            value_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            value_label.setStyleSheet(f"color: {C.WHITE};")
            footer.addWidget(label, idx, 0)
            footer.addWidget(value_label, idx, 1)

        lay.addLayout(footer)

        self._expanded_height = 320
        self._collapsed_height = 64

        try:
            self.setFixedHeight(self._expanded_height)
        except Exception:
            pass

        self._set_status("Camera offline", "lost")
        # Do not start camera automatically

    def closeEvent(self, event):
        self._stop_camera()
        super().closeEvent(event)

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self._toggle_camera()
                event.accept()
                return
        except Exception:
            pass
        return super().mousePressEvent(event)

    def _set_status(self, text: str, level: str = "searching"):
        self._status_hint_label.setText(text)
        self._status_value.setText(level.capitalize())
        colors = {
            "tracking": C.GREEN,
            "searching": "#ffb347",
            "lost": C.RED,
        }
        color = colors.get(level, "#ffb347")
        self._status_dot.setStyleSheet(f"border-radius: 6px; background: {color};")
        self._status_text.setText(level.upper())
        self._status_text.setStyleSheet(f"color: {color};")

    def _start_camera(self):
        if self._cap is not None:
            return
        try:
            import cv2
            cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY)
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            except Exception:
                pass
            if not cap.isOpened():
                cap.release()
                raise RuntimeError("camera unavailable")
            self._cap = cap
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(30)
            self._set_status("Camera ready. Move your hand to steer the cursor.", "searching")
            try:
                import pyautogui
                pyautogui.FAILSAFE = False
            except Exception:
                pass
        except Exception as exc:
            self._set_status(f"Gesture camera is offline: {exc}", "lost")

        if self._cap is not None:
            try:
                self.setFixedHeight(self._expanded_height)
            except Exception:
                pass

    def _stop_camera(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        if self._hands is not None:
            try:
                self._hands.close()
            except Exception:
                pass
            self._hands = None

        try:
            self.setFixedHeight(self._collapsed_height)
        except Exception:
            pass
        self._set_status("Camera stopped", "lost")

    def _download_hand_landmarker_model(self, model_path: Path) -> bool:
        temp_path = model_path.with_suffix(model_path.suffix + ".download")
        try:
            import urllib.request

            self._set_status("Downloading gesture model...", "searching")
            model_path.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(MODEL_DOWNLOAD_URL, timeout=60) as response:
                with open(temp_path, "wb") as out_file:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        out_file.write(chunk)
            temp_path.replace(model_path)
            return True
        except Exception as exc:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            self._set_status(
                f"Gesture model download failed: {exc}. "
                f"Put hand_landmarker.task into {model_path.parent} and restart.",
                "lost",
            )
            return False

    def _tick(self):
        if self._cap is None:
            return
        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._set_status("Camera feed dropped. Trying again…", "lost")
                return
            self._process_frame(frame)
        except Exception as exc:
            self._set_status(f"Gesture camera error: {exc}", "lost")

    def _process_frame(self, frame):
        try:
            import cv2
        except Exception as exc:
            self._set_status(f"Gesture camera unavailable: {exc}", "lost")
            return

        import importlib

        mp = None
        try:
            mp = importlib.import_module("mediapipe")
        except Exception:
            pass

        if mp is None:
            self._set_status(
                "Gesture camera unavailable: mediapipe not found. "
                "Install it into the app venv: .venv\\Scripts\\python.exe -m pip install mediapipe",
                "lost",
            )
            return

        if self._hands is None:
            HandsClass = None
            try:
                solutions = getattr(mp, "solutions", None)
                if solutions is not None and hasattr(solutions, "hands"):
                    HandsClass = solutions.hands.Hands
            except Exception:
                HandsClass = None

            if HandsClass is not None:
                try:
                    self._hands = HandsClass(
                        static_image_mode=False,
                        max_num_hands=1,
                        min_detection_confidence=0.5,
                        min_tracking_confidence=0.5,
                    )
                    self._use_tasks_api = False
                except Exception as exc:
                    self._set_status(f"Gesture init error: {exc}", "lost")
                    return
            else:
                vision = None
                try:
                    vision = importlib.import_module("mediapipe.tasks.python.vision")
                except Exception:
                    vision = None

                if vision is None or not hasattr(vision, "HandLandmarker"):
                    self._set_status(
                        "Gesture camera unavailable: mediapipe Tasks API not available. "
                        "Install mediapipe into the app venv and restart.",
                        "lost",
                    )
                    return

                model_dir = CONFIG_DIR / "models"
                model_dir.mkdir(parents=True, exist_ok=True)
                model_path = model_dir / "hand_landmarker.task"
                if not model_path.exists():
                    self._set_status("Downloading model", "searching")
                    if not self._download_hand_landmarker_model(model_path):
                        return

                try:
                    self._hands = vision.HandLandmarker.create_from_model_path(str(model_path))
                    self._use_tasks_api = True
                    self._vision_module = vision
                except Exception as exc:
                    self._set_status(f"Gesture init error: {exc}", "lost")
                    return

        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        landmarks: list[tuple[float, float, float]] = []
        confidence = 0.0

        if self._use_tasks_api:
            try:
                import numpy as np
                image_lib = importlib.import_module("mediapipe.tasks.python.vision.core.image")
                mp_image = image_lib.Image(image_lib.ImageFormat.SRGB, np.ascontiguousarray(rgb))
                results = self._hands.detect(mp_image)
            except Exception as exc:
                self._set_status(f"Gesture task detect error: {exc}", "lost")
                return
            if getattr(results, "hand_landmarks", None):
                hand_landmarks = results.hand_landmarks[0]
                for landmark in hand_landmarks:
                    landmarks.append((landmark.x, landmark.y, landmark.z))
                confidence = 1.0 if landmarks else 0.0
        else:
            results = self._hands.process(rgb)
            if getattr(results, "multi_hand_landmarks", None):
                hand_landmarks = results.multi_hand_landmarks[0]
                for landmark in hand_landmarks.landmark:
                    landmarks.append((landmark.x, landmark.y, landmark.z))
            if getattr(results, "multi_handedness", None) and results.multi_handedness:
                try:
                    confidence = float(results.multi_handedness[0].classification[0].score)
                except Exception:
                    confidence = 1.0 if landmarks else 0.0

        gesture = estimate_gesture_state(landmarks, tracker=self._gesture_tracker)
        if gesture.get("action"):
            self._handle_air_action(gesture["action"])
        if gesture.get("cursor"):
            norm = self._calibrate_and_smooth_cursor(gesture["cursor"])
            self._move_cursor(norm)
        if gesture.get("pinch_triggered"):
            self._trigger_click()
        self._prev_pinch = bool(gesture.get("pinch", False))

        self._render_hand(landmarks, gesture, confidence)

    def _handle_air_action(self, action: str):
        try:
            from actions.spotify_controller import _press_media_key
            import pyautogui

            if action == "play_pause":
                _press_media_key("playpause")
                self._show_gesture_toast("✋ Air Gesture: Play / Pause")

            elif action == "swipe_right":
                # If tracking is inverted, swiping hand to user's right moves next
                _press_media_key("nexttrack")
                try:
                    pyautogui.press("right")
                except Exception:
                    pass
                self._show_gesture_toast("👉 Air Gesture: Next Track / Slide")

            elif action == "swipe_left":
                _press_media_key("prevtrack")
                try:
                    pyautogui.press("left")
                except Exception:
                    pass
                self._show_gesture_toast("👈 Air Gesture: Previous Track / Slide")

            elif action == "volume_up":
                _press_media_key("volumeup")
                self._show_gesture_toast("👍 Air Gesture: Volume Up")

            elif action == "volume_down":
                _press_media_key("volumedown")
                self._show_gesture_toast("👎 Air Gesture: Volume Down")

            elif action == "screenshot":
                from datetime import datetime
                from pathlib import Path
                desktop_dir = Path.home() / "Desktop"
                desktop_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shot_path = desktop_dir / f"screenshot_{ts}.png"
                pyautogui.screenshot(str(shot_path))
                self._show_gesture_toast("✌️ Screenshot saved to Desktop!")

        except Exception as e:
            print(f"[AirGesture] Action '{action}' error: {e}")

    def _show_gesture_toast(self, text: str):
        try:
            if hasattr(self, "_status_hint_label"):
                self._status_hint_label.setText(text)
                self._status_hint_label.setStyleSheet("color: #ffb347; font-weight: bold;")
        except Exception:
            pass

    def _render_hand(self, landmarks: list[tuple[float, float, float]], gesture: dict, confidence: float):
        has_hand = bool(landmarks and len(landmarks) >= 21)
        if has_hand:
            if self._smoothed_landmarks is None or len(self._smoothed_landmarks) != len(landmarks):
                self._smoothed_landmarks = landmarks.copy()
            else:
                alpha = 0.32
                smoothed: list[tuple[float, float, float]] = []
                for prev, current in zip(self._smoothed_landmarks, landmarks):
                    sx, sy, sz = prev
                    tx, ty, tz = current
                    smoothed.append((sx + alpha * (tx - sx), sy + alpha * (ty - sy), sz + alpha * (tz - sz)))
                self._smoothed_landmarks = smoothed
            self._hand_canvas.set_landmarks(self._smoothed_landmarks)
            self._hand_canvas.set_hand_visible(True)
            self._hand_canvas.set_search_phase(0)
            self._set_status("Hand detected and tracking.", "tracking")
            self._confidence_value.setText(f"{int(confidence * 100)}%")
            self._gesture_value.setText(gesture.get("gesture_name", "Open Hand"))
            self._cursor_value.setText("Active" if gesture.get("cursor") else "Inactive")
        else:
            self._hand_canvas.set_hand_visible(False)
            self._search_phase = (self._search_phase + 1) % 32
            self._hand_canvas.set_search_phase(self._search_phase)
            self._set_status("Searching for hand...", "searching")
            self._confidence_value.setText("0%")
            self._gesture_value.setText("None")
            self._cursor_value.setText("Inactive")

    def _calibrate_and_smooth_cursor(self, cursor: tuple[float, float]) -> tuple[float, float]:
        raw_x = float(cursor[0])
        raw_y = float(cursor[1])

        if self._invert_cursor_x:
            raw_x = 1.0 - raw_x
        if self._invert_cursor_y:
            raw_y = 1.0 - raw_y

        try:
            s = float(self._sensitivity)
        except Exception:
            s = 2.5

        # Expand central active range so comfortable hand movements easily reach 0.0 and 1.0 edges
        mapped_x = (raw_x - 0.5) * s + 0.5
        mapped_y = (raw_y - 0.5) * s + 0.5

        mapped_x = max(0.0, min(1.0, mapped_x))
        mapped_y = max(0.0, min(1.0, mapped_y))

        if self._smoothed_cursor is None:
            self._smoothed_cursor = (mapped_x, mapped_y)
        else:
            sx, sy = self._smoothed_cursor
            dist = math.hypot(mapped_x - sx, mapped_y - sy)
            # Dynamic smoothing: high responsiveness for quick movements to reach screen corners instantly
            a = max(0.60, min(0.95, 0.60 + dist * 2.5))
            self._smoothed_cursor = (sx + a * (mapped_x - sx), sy + a * (mapped_y - sy))

        return self._smoothed_cursor

    def _set_sensitivity_level(self, level: str) -> None:
        self._sensitivity = self._sensitivity_levels.get(level, self._sensitivity_levels["Medium"])

    def _move_cursor(self, cursor):
        try:
            import pyautogui
            pyautogui.PAUSE = 0

            try:
                import ctypes
                screen_w = int(ctypes.windll.user32.GetSystemMetrics(0))
                screen_h = int(ctypes.windll.user32.GetSystemMetrics(1))
            except Exception:
                screen_w, screen_h = pyautogui.size()

            if screen_w <= 0 or screen_h <= 0:
                screen_w, screen_h = pyautogui.size()

            nx = max(0.0, min(1.0, float(cursor[0])))
            ny = max(0.0, min(1.0, float(cursor[1])))

            target_x = int(round(nx * (screen_w - 1)))
            target_y = int(round(ny * (screen_h - 1)))

            target_x = max(0, min(screen_w - 1, target_x))
            target_y = max(0, min(screen_h - 1, target_y))

            dead_zone = 2
            if self._last_screen_pos is not None:
                last_x, last_y = self._last_screen_pos
                if abs(target_x - last_x) <= dead_zone and abs(target_y - last_y) <= dead_zone:
                    return

            self._last_screen_pos = (target_x, target_y)
            pyautogui.moveTo(target_x, target_y, _pause=False)
        except Exception:
            pass

    def _trigger_click(self):
        try:
            import pyautogui
            pyautogui.click(button="left")
        except Exception:
            pass

    def _toggle_camera(self):
        if self._cap is None:
            self._start_camera()
        else:
            self._stop_camera()


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

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._screen_capture_result = b""
        import threading
        self._screen_capture_event = threading.Event()
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for Brahma Evo", str(Path.home()),
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
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✖")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str, str)

    def __init__(self, parent=None, defaults: dict | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        self._defaults = defaults or {}
        self._detected = {"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux")
        self._sel_os = self._defaults.get("os_system", self._detected)

        self._stack = QStackedWidget(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._stack)

        self._build_stage1()
        self._build_stage_identity()
        self._build_stage_owner()
        self._build_stage_behavior()
        self._build_stage_shared()
        self._build_stage2()
        self._build_stage3()
        self._build_stage4()

        self._stack.setCurrentIndex(0)
        QTimer.singleShot(1000, self._start_stage1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Heavy dark overlay to completely hide dashboard UI and make a full faded background
        painter.fillRect(self.rect(), QColor(3, 5, 8, 245))

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        # Add a subtle gold vignette ring over the full faded background
        ring_grad = QRadialGradient(cx, cy, 320.0)
        ring_grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        ring_grad.setColorAt(0.7, QColor(0, 0, 0, 0))
        ring_grad.setColorAt(0.85, QColor(0, 229, 255, 12))
        ring_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(ring_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), 320.0, 320.0)

    def _call_js(self, func_call):
        try:
            p = self.parentWidget()
            while p is not None:
                if hasattr(p, '_background'):
                    p._background.page().runJavaScript(func_call)
                    return
                p = p.parentWidget()
        except Exception:
            pass

    # ── STAGE 1: System Check ──────────────────────────────────────
    def _build_stage1(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        # Push text to the bottom half (below reactor)
        lay.addStretch(3)

        self._s1_container = QFrame()
        self._s1_container.setStyleSheet("""
            QFrame {
                background: rgba(5, 8, 12, 180);
                border: 1px solid rgba(0, 229, 255, 0.08);
                border-radius: 16px;
            }
        """)
        self._s1_container.setFixedWidth(420)
        clay = QVBoxLayout(self._s1_container)
        clay.setContentsMargins(30, 24, 30, 24)
        clay.setSpacing(0)

        self._s1_lbl = QLabel("")
        self._s1_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._s1_lbl.setFont(QFont("Consolas", 12))
        self._s1_lbl.setStyleSheet("color: rgba(0, 229, 255, 0.9); background: transparent; border: none;")
        self._s1_lbl.setWordWrap(True)
        clay.addWidget(self._s1_lbl)

        lay.addWidget(self._s1_container, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        self._stack.addWidget(page)

    def _start_stage1(self):
        self._s1_lines = [
            ("Scanning local configuration...", "#00e5ff", False),
            ("✓  " + self._detected.capitalize() + " detected", "#37ff5f", False),
            ("✓  GPU acceleration enabled", "#37ff5f", False),
            ("✓  Network online", "#37ff5f", False),
            ("Looking for AI provider...", "#00e5ff", False),
            ("✕  No provider configured", "#ff3b30", True),
            ("", "", False),
            ("One final step is required\nbefore I can think.", "#ffffff", True),
        ]
        self._s1_idx = 0
        self._s1_text_parts = []
        self._s1_timer = QTimer(self)
        self._s1_timer.timeout.connect(self._s1_tick)
        self._s1_timer.start(900)
        self._s1_tick()

    def _s1_tick(self):
        if self._s1_idx < len(self._s1_lines):
            line_text, color, is_special = self._s1_lines[self._s1_idx]

            if line_text == "":
                self._s1_idx += 1
                return

            if is_special and "✕" in line_text:
                self._call_js("if(window.losePower) window.losePower();")
            elif is_special and "final step" in line_text:
                self._call_js("if(window.triggerPulse) window.triggerPulse();")

            size = "14px" if is_special and "final" in line_text else "12px"
            weight = "bold" if is_special else "normal"
            spacing = "margin-top: 16px;" if is_special and "final" in line_text else "margin-top: 4px;"

            self._s1_text_parts.append(
                f'<div style="color:{color}; font-size:{size}; font-weight:{weight}; font-family:Consolas; {spacing}">{line_text}</div>'
            )
            self._s1_lbl.setText("".join(self._s1_text_parts))

            self._s1_idx += 1
        else:
            self._s1_timer.stop()
            QTimer.singleShot(2500, self._finish_stage1)

    # ── STAGE 2: Provider Selection ────────────────────────────────

    # ── STAGE 1.1: Assistant Identity ────────────────────────────────
    def _build_stage_identity(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(2)

        title = QLabel("DEFINE YOUR ASSISTANT")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: rgba(255,255,255,0.7); background: transparent; letter-spacing: 2px;")
        lay.addWidget(title)
        
        sub = QLabel("Every assistant needs an identity.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Segoe UI", 10))
        sub.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent;")
        lay.addWidget(sub)
        lay.addSpacing(20)

        form_lay = QGridLayout()
        form_lay.setSpacing(16)
        
        lbl_ast = QLabel("Assistant Name")
        lbl_ast.setStyleSheet("color: #ffaa30;")
        self._inp_ast = QLineEdit(identity.get_assistant_name())
        self._inp_ast.setStyleSheet("background: rgba(255,255,255,0.1); color: #fff; padding: 6px; border-radius: 4px;")
        form_lay.addWidget(lbl_ast, 0, 0)
        form_lay.addWidget(self._inp_ast, 0, 1)

        lbl_app = QLabel("Application Name")
        lbl_app.setStyleSheet("color: #ffaa30;")
        self._inp_app = QLineEdit(identity.get_application_name())
        self._inp_app.setStyleSheet("background: rgba(255,255,255,0.1); color: #fff; padding: 6px; border-radius: 4px;")
        form_lay.addWidget(lbl_app, 1, 0)
        form_lay.addWidget(self._inp_app, 1, 1)

        container = QWidget()
        container.setFixedWidth(400)
        container.setLayout(form_lay)
        lay.addWidget(container, 0, Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton("CONTINUE →")
        btn.setFixedSize(160, 40)
        btn.setStyleSheet("background: rgba(255, 170, 48, 0.2); color: #ffaa30; border: 1px solid #ffaa30; border-radius: 20px;")
        btn.clicked.connect(self._save_identity_and_next)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        
        lay.addStretch(2)
        self._stack.addWidget(page)

    def _save_identity_and_next(self):
        identity.set_assistant_name(self._inp_ast.text().strip() or "Brahma")
        identity.set_application_name(self._inp_app.text().strip() or "Brahma Evo")
        self._stack.setCurrentIndex(2)

    # ── STAGE 1.2: Owner Profile ────────────────────────────────
    def _build_stage_owner(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(2)

        title = QLabel("WHO AM I ASSISTING?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: rgba(255,255,255,0.7); background: transparent; letter-spacing: 2px;")
        lay.addWidget(title)
        
        sub = QLabel("Tell me a little about yourself so I can work better with you.")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Segoe UI", 10))
        sub.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent;")
        lay.addWidget(sub)
        lay.addSpacing(20)

        form_lay = QGridLayout()
        form_lay.setSpacing(16)
        
        lbl_own = QLabel("Your Name")
        lbl_own.setStyleSheet("color: #ffaa30;")
        self._inp_own = QLineEdit(identity.get_owner_name())
        self._inp_own.setStyleSheet("background: rgba(255,255,255,0.1); color: #fff; padding: 6px; border-radius: 4px;")
        form_lay.addWidget(lbl_own, 0, 0)
        form_lay.addWidget(self._inp_own, 0, 1)

        lbl_role = QLabel("Your Role")
        lbl_role.setStyleSheet("color: #ffaa30;")
        self._inp_role = QLineEdit(identity.get_owner_role())
        self._inp_role.setPlaceholderText("e.g. Student, Developer")
        self._inp_role.setStyleSheet("background: rgba(255,255,255,0.1); color: #fff; padding: 6px; border-radius: 4px;")
        form_lay.addWidget(lbl_role, 1, 0)
        form_lay.addWidget(self._inp_role, 1, 1)

        container = QWidget()
        container.setFixedWidth(400)
        container.setLayout(form_lay)
        lay.addWidget(container, 0, Qt.AlignmentFlag.AlignCenter)

        btn = QPushButton("CONTINUE →")
        btn.setFixedSize(160, 40)
        btn.setStyleSheet("background: rgba(255, 170, 48, 0.2); color: #ffaa30; border: 1px solid #ffaa30; border-radius: 20px;")
        btn.clicked.connect(self._save_owner_and_next)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        
        lay.addStretch(2)
        self._stack.addWidget(page)

    def _save_owner_and_next(self):
        identity.set_owner_name(self._inp_own.text().strip())
        identity.set_owner_role(self._inp_role.text().strip())
        self._stack.setCurrentIndex(3)

    # ── STAGE 1.3: Behavior ────────────────────────────────
    def _build_stage_behavior(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(2)

        title = QLabel("HOW SHOULD I ASSIST YOU?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: rgba(255,255,255,0.7); background: transparent; letter-spacing: 2px;")
        lay.addWidget(title)
        lay.addSpacing(20)

        self._combo_mode = QComboBox()
        self._combo_mode.addItems(["professional", "casual", "technical", "minimal", "proactive"])
        self._combo_mode.setCurrentText(identity.get_behavior_mode())
        self._combo_mode.setStyleSheet("background: rgba(255,255,255,0.1); color: #fff; padding: 6px; border-radius: 4px;")
        self._combo_mode.setFixedWidth(300)
        lay.addWidget(self._combo_mode, 0, Qt.AlignmentFlag.AlignCenter)
        
        sub = QLabel("Custom Instructions (Optional)")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #ffaa30; margin-top: 10px;")
        lay.addWidget(sub)
        
        self._inp_custom = QTextEdit(identity.get_custom_instructions())
        self._inp_custom.setFixedSize(400, 80)
        self._inp_custom.setStyleSheet("background: rgba(255,255,255,0.1); color: #fff; padding: 6px; border-radius: 4px;")
        lay.addWidget(self._inp_custom, 0, Qt.AlignmentFlag.AlignCenter)

        lay.addSpacing(20)
        btn = QPushButton("CONTINUE →")
        btn.setFixedSize(160, 40)
        btn.setStyleSheet("background: rgba(255, 170, 48, 0.2); color: #ffaa30; border: 1px solid #ffaa30; border-radius: 20px;")
        btn.clicked.connect(self._save_behavior_and_next)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
        
        lay.addStretch(2)
        self._stack.addWidget(page)

    def _save_behavior_and_next(self):
        identity.set_behavior_mode(self._combo_mode.currentText())
        identity.set_custom_instructions(self._inp_custom.toPlainText().strip())
        self._stack.setCurrentIndex(4)

    # ── STAGE 1.4: Shared Computer ────────────────────────────────
    def _build_stage_shared(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(2)

        title = QLabel("WHO USES THIS COMPUTER?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: rgba(255,255,255,0.7); background: transparent; letter-spacing: 2px;")
        lay.addWidget(title)
        lay.addSpacing(20)
        
        btn_personal = QPushButton("THIS IS MY PERSONAL COMPUTER")
        btn_personal.setFixedSize(300, 50)
        btn_personal.setStyleSheet("background: rgba(255, 170, 48, 0.1); color: #ffaa30; border: 1px solid #ffaa30; border-radius: 8px;")
        btn_personal.clicked.connect(lambda: self._save_shared_and_next(False))
        lay.addWidget(btn_personal, 0, Qt.AlignmentFlag.AlignCenter)
        
        lay.addSpacing(10)
        
        btn_shared = QPushButton("THIS IS A SHARED COMPUTER")
        btn_shared.setFixedSize(300, 50)
        btn_shared.setStyleSheet("background: rgba(255,255,255, 0.05); color: #fff; border: 1px solid rgba(255,255,255,0.2); border-radius: 8px;")
        btn_shared.clicked.connect(lambda: self._save_shared_and_next(True))
        lay.addWidget(btn_shared, 0, Qt.AlignmentFlag.AlignCenter)
        
        lay.addStretch(2)
        self._stack.addWidget(page)
        
    def _save_shared_and_next(self, shared: bool):
        identity.set_shared_computer(shared)
        self._stack.setCurrentIndex(5)


    def _finish_stage1(self):
        if not identity.is_setup_complete():
            self._stack.setCurrentIndex(1)
        else:
            self._stack.setCurrentIndex(5)

    def _build_stage2(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addStretch(3)

        title = QLabel("SELECT PRIMARY INTELLIGENCE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet("color: rgba(255,255,255,0.5); background: transparent; border: none; letter-spacing: 4px;")
        lay.addWidget(title)
        lay.addSpacing(24)

        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(20)
        cards_lay.addStretch()

        # ── Gemini Card ──
        gem_card = QFrame()
        gem_card.setFixedSize(260, 160)
        gem_card.setStyleSheet("""
            QFrame {
                background: rgba(0, 229, 255, 0.06);
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 16px;
            }
            QFrame:hover {
                background: rgba(0, 229, 255, 0.12);
                border: 1px solid rgba(0, 229, 255, 0.5);
            }
        """)
        gem_card.setCursor(Qt.CursorShape.PointingHandCursor)
        glay = QVBoxLayout(gem_card)
        glay.setContentsMargins(22, 18, 22, 18)
        glay.setSpacing(4)

        gt = QLabel("Google Gemini")
        gt.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        gt.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
        glay.addWidget(gt)

        gs = QLabel("★★★★★  Recommended")
        gs.setFont(QFont("Segoe UI", 9))
        gs.setStyleSheet("color: rgba(0, 229, 255,0.7); background: transparent; border: none;")
        glay.addWidget(gs)

        gd = QLabel("Primary Intelligence")
        gd.setFont(QFont("Segoe UI", 9))
        gd.setStyleSheet("color: rgba(255,255,255,0.35); background: transparent; border: none;")
        glay.addWidget(gd)

        glay.addStretch()

        gc = QLabel("Connect →")
        gc.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        gc.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
        glay.addWidget(gc)

        # Make the whole card clickable via a transparent button overlay
        gem_btn = QPushButton(gem_card)
        gem_btn.setGeometry(0, 0, 260, 160)
        gem_btn.setStyleSheet("background: transparent; border: none;")
        gem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gem_btn.clicked.connect(self._goto_stage3)

        cards_lay.addWidget(gem_card)

        # ── OpenRouter Card ──
        or_card = QFrame()
        or_card.setFixedSize(260, 160)
        or_card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
            QFrame:hover {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.2);
            }
        """)
        or_card.setCursor(Qt.CursorShape.PointingHandCursor)
        olay = QVBoxLayout(or_card)
        olay.setContentsMargins(22, 18, 22, 18)
        olay.setSpacing(4)

        ot = QLabel("OpenRouter")
        ot.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        ot.setStyleSheet("color: rgba(255,255,255,0.8); background: transparent; border: none;")
        olay.addWidget(ot)

        od = QLabel("Multiple Models")
        od.setFont(QFont("Segoe UI", 9))
        od.setStyleSheet("color: rgba(255,255,255,0.3); background: transparent; border: none;")
        olay.addWidget(od)

        od2 = QLabel("Optional · Secondary")
        od2.setFont(QFont("Segoe UI", 9))
        od2.setStyleSheet("color: rgba(255,255,255,0.2); background: transparent; border: none;")
        olay.addWidget(od2)

        olay.addStretch()

        oc = QLabel("Configure Later →")
        oc.setFont(QFont("Segoe UI", 11))
        oc.setStyleSheet("color: rgba(255,255,255,0.35); background: transparent; border: none;")
        olay.addWidget(oc)

        cards_lay.addWidget(or_card)
        cards_lay.addStretch()

        lay.addLayout(cards_lay)
        lay.addStretch(1)
        self._stack.addWidget(page)

    def _goto_stage3(self):
        self._call_js("if(window.triggerPulse) window.triggerPulse();")
        self._stack.setCurrentIndex(6)

    # ── STAGE 3 & 4: API Input + Auth ──────────────────────────────
    def _build_stage3(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addStretch(3)

        self._s3_box = QFrame()
        self._s3_box.setFixedSize(480, 260)
        self._s3_box.setStyleSheet("""
            QFrame {
                background: rgba(8, 10, 16, 220);
                border: 1px solid rgba(0, 229, 255, 0.2);
                border-radius: 20px;
            }
        """)
        blay = QVBoxLayout(self._s3_box)
        blay.setContentsMargins(32, 28, 32, 28)
        blay.setSpacing(6)

        self._s3_title = QLabel("Google Gemini")
        self._s3_title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self._s3_title.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
        blay.addWidget(self._s3_title)

        self._s3_sub = QLabel("Paste your Neural Key")
        self._s3_sub.setFont(QFont("Segoe UI", 10))
        self._s3_sub.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent; border: none;")
        blay.addWidget(self._s3_sub)

        blay.addSpacing(16)

        # Input row
        input_row = QHBoxLayout()
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("Paste API key here...")
        self._key_input.setFont(QFont("Consolas", 12))
        self._key_input.setFixedHeight(48)
        self._key_input.setStyleSheet("""
            QLineEdit {
                background: rgba(0, 229, 255, 0.04);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 12px;
                padding: 0 16px;
                letter-spacing: 1px;
                selection-background-color: rgba(0, 229, 255, 0.3);
            }
            QLineEdit:focus {
                border: 1px solid rgba(0, 229, 255, 0.6);
                background: rgba(0, 229, 255, 0.06);
            }
        """)
        self._key_input.setText((self._defaults.get("gemini_api_key") or "").strip())
        self._key_input.textChanged.connect(self._on_key_changed)
        input_row.addWidget(self._key_input)

        toggle_pw = QPushButton("👁")
        toggle_pw.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle_pw.setFixedSize(36, 48)
        toggle_pw.setStyleSheet("QPushButton { background: transparent; border: none; color: rgba(255,255,255,0.3); font-size: 16px; } QPushButton:hover { color: #00e5ff; }")
        def _toggle():
            if self._key_input.echoMode() == QLineEdit.EchoMode.Password:
                self._key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            else:
                self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        toggle_pw.clicked.connect(_toggle)
        input_row.addWidget(toggle_pw)
        blay.addLayout(input_row)

        # Status + link row
        status_row = QHBoxLayout()
        self._s3_status = QLabel("")
        self._s3_status.setFont(QFont("Consolas", 10))
        self._s3_status.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
        status_row.addWidget(self._s3_status)

        status_row.addStretch()

        hint = QLabel("<a href='https://aistudio.google.com/app/apikey' style='color: rgba(0, 229, 255,0.5); text-decoration: none; font-size: 10px;'>Get API Key →</a>")
        hint.setOpenExternalLinks(True)
        hint.setStyleSheet("background: transparent; border: none;")
        status_row.addWidget(hint)
        blay.addLayout(status_row)

        blay.addStretch()

        lay.addWidget(self._s3_box, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)

        # Intro page (hidden initially, will replace s3_box)
        self._intro_widget = QWidget(page)
        self._intro_widget.hide()
        intro_lay = QVBoxLayout(self._intro_widget)
        intro_lay.setContentsMargins(0, 0, 0, 0)
        intro_lay.setSpacing(12)

        self._intro_lines = []
        for txt in ["Identity confirmed.", "Hello.", "I'm Brahma Evo.", "Ready whenever you are."]:
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if txt == "I'm Brahma Evo.":
                lbl.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
                lbl.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
            else:
                lbl.setFont(QFont("Segoe UI", 14))
                lbl.setStyleSheet("color: rgba(255,255,255,0.6); background: transparent; border: none;")
            lbl.hide()
            intro_lay.addWidget(lbl)
            self._intro_lines.append(lbl)

        intro_lay.addSpacing(20)

        self._launch_btn = QPushButton("Launch Brahma Evo →")
        self._launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_btn.setFixedSize(220, 48)
        self._launch_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._launch_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.1);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.4);
                border-radius: 24px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.25);
                border: 1px solid #00e5ff;
            }
        """)
        self._launch_btn.hide()
        self._launch_btn.clicked.connect(self._start_ignition)
        intro_lay.addWidget(self._launch_btn, 0, Qt.AlignmentFlag.AlignCenter)

        self._stack.addWidget(page)

    def _on_key_changed(self, text):
        if len(text) > 10 and not hasattr(self, "_authenticating"):
            self._authenticating = True
            self._key_input.setReadOnly(True)
            self._s3_sub.setText("Authenticating...")
            self._s3_sub.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
            self._auth_step = 0
            self._auth_timer = QTimer(self)
            self._auth_timer.timeout.connect(self._auth_tick)
            self._auth_timer.start(250)

    def _auth_tick(self):
        bars = [
            "█░░░░░░░░░░░░░░",
            "████░░░░░░░░░░░",
            "████████░░░░░░░",
            "███████████░░░░",
            "███████████████",
        ]
        if self._auth_step < len(bars):
            self._s3_status.setText(bars[self._auth_step])
            if self._auth_step % 2 == 0:
                self._call_js("if(window.triggerPulse) window.triggerPulse();")
            self._auth_step += 1
        else:
            self._auth_timer.stop()
            self._s3_status.setText("✓ Identity Verified")
            self._s3_status.setStyleSheet("color: #37ff5f; background: transparent; border: none;")
            self._s3_title.setText("✓ Google Gemini")
            self._s3_title.setStyleSheet("color: #37ff5f; background: transparent; border: none;")
            self._s3_sub.setText("Gemini 2.5 Pro  ·  Ready")
            self._s3_sub.setStyleSheet("color: rgba(55,255,95,0.6); background: transparent; border: none;")
            self._key_input.setStyleSheet("""
                QLineEdit {
                    background: rgba(55, 255, 95, 0.04);
                    color: #37ff5f;
                    border: 1px solid rgba(55, 255, 95, 0.3);
                    border-radius: 12px;
                    padding: 0 16px;
                }
            """)
            self._s3_box.setStyleSheet("""
                QFrame {
                    background: rgba(8, 10, 16, 220);
                    border: 1px solid rgba(55, 255, 95, 0.2);
                    border-radius: 20px;
                }
            """)
            QTimer.singleShot(1800, self._show_or_prompt)


    def _show_or_prompt(self):
        """After Gemini verified, ask if user wants to add OpenRouter too."""
        self._s3_box.hide()
        page = self._stack.widget(6)
        lay = page.layout()

        self._or_prompt_widget = QFrame()
        self._or_prompt_widget.setFixedSize(460, 200)
        self._or_prompt_widget.setStyleSheet("""
            QFrame {
                background: rgba(8, 10, 16, 220);
                border: 1px solid rgba(0, 229, 255, 0.15);
                border-radius: 20px;
            }
        """)
        prom_lay = QVBoxLayout(self._or_prompt_widget)
        prom_lay.setContentsMargins(32, 28, 32, 24)
        prom_lay.setSpacing(8)

        q_title = QLabel("Secondary Intelligence")
        q_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        q_title.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        prom_lay.addWidget(q_title)

        q_sub = QLabel("Would you like to configure OpenRouter as well?\nThis is optional and can be done later in Settings.")
        q_sub.setFont(QFont("Segoe UI", 10))
        q_sub.setWordWrap(True)
        q_sub.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent; border: none;")
        prom_lay.addWidget(q_sub)

        prom_lay.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        skip_btn = QPushButton("Skip")
        skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        skip_btn.setFixedHeight(42)
        skip_btn.setFont(QFont("Segoe UI", 11))
        skip_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: rgba(255,255,255,0.4);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 12px;
                padding: 0 24px;
            }
            QPushButton:hover {
                color: rgba(255,255,255,0.7);
                border: 1px solid rgba(255,255,255,0.25);
            }
        """)
        skip_btn.clicked.connect(self._skip_or)
        btn_row.addWidget(skip_btn)

        yes_btn = QPushButton("Configure OpenRouter")
        yes_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        yes_btn.setFixedHeight(42)
        yes_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        yes_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.1);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 12px;
                padding: 0 24px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.2);
                border: 1px solid rgba(0, 229, 255, 0.6);
            }
        """)
        yes_btn.clicked.connect(self._show_or_input)
        btn_row.addWidget(yes_btn)

        prom_lay.addLayout(btn_row)

        lay.insertWidget(lay.count() - 1, self._or_prompt_widget, 0, Qt.AlignmentFlag.AlignCenter)

    def _skip_or(self):
        """User chose to skip OpenRouter, go to color selection."""
        self._or_key_value = ""
        self._or_prompt_widget.hide()
        self._show_color_stage()

    def _show_or_input(self):
        """User wants to add OpenRouter."""
        self._or_prompt_widget.hide()
        page = self._stack.widget(6)
        lay = page.layout()

        self._or_box = QFrame()
        self._or_box.setFixedSize(480, 230)
        self._or_box.setStyleSheet("""
            QFrame {
                background: rgba(8, 10, 16, 220);
                border: 1px solid rgba(0, 229, 255, 0.2);
                border-radius: 20px;
            }
        """)
        or_blay = QVBoxLayout(self._or_box)
        or_blay.setContentsMargins(32, 28, 32, 28)
        or_blay.setSpacing(6)

        or_title = QLabel("OpenRouter")
        or_title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        or_title.setStyleSheet("color: rgba(255,255,255,0.8); background: transparent; border: none;")
        or_blay.addWidget(or_title)

        or_sub = QLabel("Paste your OpenRouter API Key")
        or_sub.setFont(QFont("Segoe UI", 10))
        or_sub.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent; border: none;")
        or_blay.addWidget(or_sub)

        or_blay.addSpacing(12)

        self._or_input = QLineEdit()
        self._or_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._or_input.setPlaceholderText("sk-or-************************")
        self._or_input.setFont(QFont("Consolas", 12))
        self._or_input.setFixedHeight(48)
        self._or_input.setStyleSheet("""
            QLineEdit {
                background: rgba(255, 255, 255, 0.03);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 12px;
                padding: 0 16px;
                letter-spacing: 1px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(0, 229, 255, 0.5);
            }
        """)
        self._or_input.setText((self._defaults.get("openrouter_api_key") or "").strip())
        or_blay.addWidget(self._or_input)

        or_blay.addStretch()

        or_btn_row = QHBoxLayout()
        or_skip = QPushButton("Skip")
        or_skip.setCursor(Qt.CursorShape.PointingHandCursor)
        or_skip.setFixedHeight(42)
        or_skip.setFont(QFont("Segoe UI", 11))
        or_skip.setStyleSheet("""
            QPushButton { background: transparent; color: rgba(255,255,255,0.4); border: none; }
            QPushButton:hover { color: rgba(255,255,255,0.7); }
        """)
        or_skip.clicked.connect(self._skip_or)
        or_btn_row.addWidget(or_skip)

        or_save = QPushButton("Save & Continue")
        or_save.setCursor(Qt.CursorShape.PointingHandCursor)
        or_save.setFixedHeight(42)
        or_save.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        or_save.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.1);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 12px;
                padding: 0 24px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.2);
                border: 1px solid rgba(0, 229, 255, 0.6);
            }
        """)
        or_save.clicked.connect(self._save_or_key)
        or_btn_row.addWidget(or_save)

        or_blay.addLayout(or_btn_row)

        lay.insertWidget(lay.count() - 1, self._or_box, 0, Qt.AlignmentFlag.AlignCenter)

    def _save_or_key(self):
        self._or_key_value = self._or_input.text().strip()
        self._or_box.hide()
        self._show_color_stage()

    def _show_color_stage(self):
        """Show color selection."""
        page = self._stack.widget(6)
        lay = page.layout()

        self._color_box = QFrame()
        self._color_box.setFixedSize(480, 230)
        self._color_box.setStyleSheet("""
            QFrame {
                background: rgba(8, 10, 16, 220);
                border: 1px solid rgba(0, 229, 255, 0.2);
                border-radius: 20px;
            }
        """)
        c_blay = QVBoxLayout(self._color_box)
        c_blay.setContentsMargins(32, 28, 32, 28)
        c_blay.setSpacing(6)

        c_title = QLabel("CHOOSE AESTHETIC")
        c_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        c_title.setStyleSheet("color: rgba(255,255,255,0.8); background: transparent; border: none;")
        c_blay.addWidget(c_title)

        c_sub = QLabel("Select your preferred neural color palette.")
        c_sub.setFont(QFont("Segoe UI", 10))
        c_sub.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent; border: none;")
        c_blay.addWidget(c_sub)

        c_blay.addSpacing(12)
        
        self._setup_color_btn = QPushButton("Pick Custom Color")
        self._setup_color_btn.setFixedHeight(48)
        self._setup_color_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._setup_color_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI};
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 12px;
            }}
        """)
        self._setup_color_btn.clicked.connect(self._pick_setup_color)
        c_blay.addWidget(self._setup_color_btn)

        c_blay.addStretch()

        c_btn_row = QHBoxLayout()
        c_btn_row.addStretch()
        c_continue = QPushButton("Continue →")
        c_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        c_continue.setFixedHeight(42)
        c_continue.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        c_continue.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.1);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 12px;
                padding: 0 24px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.2);
                border: 1px solid rgba(0, 229, 255, 0.6);
            }
        """)
        c_continue.clicked.connect(self._finish_color_stage)
        c_btn_row.addWidget(c_continue)
        
        c_blay.addLayout(c_btn_row)

        lay.insertWidget(lay.count() - 1, self._color_box, 0, Qt.AlignmentFlag.AlignCenter)
        
    def _pick_setup_color(self):
        color = QColorDialog.getColor(QColor(C.PRI), self, "Select App Theme Color")
        if color.isValid():
            hex_col = color.name()
            self._setup_color_btn.setStyleSheet(f"QPushButton {{ background: {hex_col}; color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 12px; }}")
            try:
                settings = {}
                if APP_SETTINGS_FILE.exists():
                    with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                settings["app_theme"] = hex_col
                with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=4)
                C.load_theme(hex_col)
            except:
                pass
                
    def _finish_color_stage(self):
        self._color_box.hide()
        self._show_intro_final()

    def _show_intro_final(self):
        """Show the Brahma Evo intro sequence."""
        page = self._stack.widget(6)
        lay = page.layout()
        self._intro_widget.setParent(None)
        lay.insertWidget(lay.count() - 1, self._intro_widget, 0, Qt.AlignmentFlag.AlignCenter)
        self._intro_widget.show()
        self._intro_reveal_idx = 0
        self._intro_timer = QTimer(self)
        self._intro_timer.timeout.connect(self._intro_tick)
        self._intro_timer.start(600)

    def _show_intro(self):
        self._s3_box.hide()
        page = self._stack.widget(6)
        lay = page.layout()
        self._intro_widget.setParent(None)
        lay.insertWidget(lay.count() - 1, self._intro_widget, 0, Qt.AlignmentFlag.AlignCenter)
        self._intro_widget.show()
        self._intro_reveal_idx = 0
        self._intro_timer = QTimer(self)
        self._intro_timer.timeout.connect(self._intro_tick)
        self._intro_timer.start(600)

    def _intro_tick(self):
        if self._intro_reveal_idx < len(self._intro_lines):
            self._intro_lines[self._intro_reveal_idx].show()
            if self._intro_reveal_idx == 2:
                self._call_js("if(window.triggerPulse) window.triggerPulse();")
            self._intro_reveal_idx += 1
        else:
            self._intro_timer.stop()
            self._launch_btn.show()

    # ── STAGE 6: Neural Link Ignition ──────────────────────────────
    def _build_stage4(self):
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addStretch(3)

        self._s4_container = QFrame()
        self._s4_container.setFixedWidth(360)
        self._s4_container.setStyleSheet("""
            QFrame {
                background: rgba(5, 8, 12, 180);
                border: 1px solid rgba(0, 229, 255, 0.12);
                border-radius: 16px;
            }
        """)
        c4lay = QVBoxLayout(self._s4_container)
        c4lay.setContentsMargins(30, 24, 30, 24)
        c4lay.setSpacing(8)

        self._s4_title = QLabel("ESTABLISHING NEURAL LINK")
        self._s4_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._s4_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._s4_title.setStyleSheet("color: rgba(0, 229, 255,0.7); background: transparent; border: none; letter-spacing: 3px;")
        c4lay.addWidget(self._s4_title)
        c4lay.addSpacing(12)

        self._module_labels = {}
        for mod in ["MEMORY", "VOICE", "VISION", "AUTOMATION", "REASONING"]:
            row = QHBoxLayout()
            name_lbl = QLabel(mod)
            name_lbl.setFont(QFont("Consolas", 11))
            name_lbl.setStyleSheet("color: rgba(255,255,255,0.2); background: transparent; border: none;")
            name_lbl.setFixedWidth(130)
            row.addWidget(name_lbl)

            bar_lbl = QLabel("░░░░░░░░░░")
            bar_lbl.setFont(QFont("Consolas", 11))
            bar_lbl.setStyleSheet("color: rgba(0, 229, 255,0.15); background: transparent; border: none;")
            row.addWidget(bar_lbl)
            row.addStretch()

            c4lay.addLayout(row)
            self._module_labels[mod] = (name_lbl, bar_lbl)

        lay.addWidget(self._s4_container, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        self._stack.addWidget(page)

    def _start_ignition(self):
        self._stack.setCurrentIndex(7)
        self._call_js("if(window.setReactorSpeed) window.setReactorSpeed(5.0);")

        self._ignite_step = 0
        self._module_order = ["MEMORY", "VOICE", "VISION", "AUTOMATION", "REASONING"]
        self._ignite_timer = QTimer(self)
        self._ignite_timer.timeout.connect(self._ignite_tick)
        self._ignite_timer.start(600)

    def _ignite_tick(self):
        if self._ignite_step < len(self._module_order):
            mod = self._module_order[self._ignite_step]
            name_lbl, bar_lbl = self._module_labels[mod]
            name_lbl.setStyleSheet("color: #00e5ff; background: transparent; border: none; font-weight: bold;")
            bar_lbl.setText("██████████")
            bar_lbl.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
            self._call_js(f"if(window.setReactorSpeed) window.setReactorSpeed({5.0 + self._ignite_step * 4});")
            self._call_js("if(window.triggerPulse) window.triggerPulse();")
            self._ignite_step += 1
        else:
            self._ignite_timer.stop()
            self._s4_title.setText("NEURAL LINK ESTABLISHED")
            self._s4_title.setStyleSheet("color: #37ff5f; background: transparent; border: none; letter-spacing: 3px; font-weight: bold;")
            self._s4_container.setStyleSheet("""
                QFrame {
                    background: rgba(5, 8, 12, 180);
                    border: 1px solid rgba(55, 255, 95, 0.2);
                    border-radius: 16px;
                }
            """)
            for mod in self._module_order:
                n, b = self._module_labels[mod]
                n.setStyleSheet("color: #37ff5f; background: transparent; border: none; font-weight: bold;")
                b.setStyleSheet("color: #37ff5f; background: transparent; border: none;")
            self._call_js("if(window.dissolveReactor) window.dissolveReactor();")
            QTimer.singleShot(1200, lambda: self.done.emit(self._key_input.text().strip(), getattr(self, "_or_key_value", ""), self._sel_os))




class CommandBar(QWidget):
    submitted = pyqtSignal(str)
    attach_clicked = pyqtSignal()
    mic_clicked = pyqtSignal()
    developer_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("CommandBar")
        self.setFixedSize(580, 48)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("CommandBarFrame")
        frame.setStyleSheet(f"""
            QFrame#CommandBarFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(6, 6, 10, 252),
                    stop:0.3 rgba(12, 11, 16, 252),
                    stop:0.7 rgba(12, 11, 16, 252),
                    stop:1 rgba(6, 6, 10, 252));
                border: 1px solid rgba(0, 229, 255, 0.22);
                border-radius: 20px;
            }}
        """)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        # Brahma Evo mini logo
        logo_frame = QFrame()
        logo_frame.setFixedSize(32, 32)
        logo_frame.setStyleSheet("""
            QFrame {
                background: rgba(0, 229, 255, 0.06);
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 16px;
            }
        """)
        logo_lay = QVBoxLayout(logo_frame)
        logo_lay.setContentsMargins(0, 0, 0, 0)
        logo_lbl = QLabel("\u092C\u094D\u0930")  # ब्र (short Hindi)
        logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_lbl.setFont(QFont("Nirmala UI", 9, QFont.Weight.Bold))
        logo_lbl.setStyleSheet("color: #00e5ff; background: transparent; border: none;")
        logo_lay.addWidget(logo_lbl)
        lay.addWidget(logo_frame)

        # Input field
        self._input = QLineEdit()
        self._input.setPlaceholderText("Tell Brahma Evo what to do...")
        self._input.setFont(QFont("Segoe UI", 9))
        self._input.setFixedHeight(32)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 16px;
                padding: 0 14px;
                selection-background-color: rgba(0, 229, 255, 0.25);
            }}
            QLineEdit:focus {{
                border: 1px solid rgba(0, 229, 255, 0.45);
                background: rgba(0, 229, 255, 0.03);
            }}
        """)
        self._input.returnPressed.connect(self._submit)
        lay.addWidget(self._input, stretch=1)

        # Action buttons container
        btn_style_ghost = f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.03);
                color: rgba(255, 255, 255, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.12);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.4);
            }}
        """

        attach = QPushButton()
        attach.setFixedSize(28, 28)
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setToolTip("Attach file")
        attach.setIcon(QIcon(_icon_pixmap("attach", 13)))
        attach.setIconSize(QSize(13, 13))
        attach.setStyleSheet(btn_style_ghost)
        attach.clicked.connect(self.attach_clicked.emit)
        lay.addWidget(attach)

        mic = QPushButton()
        mic.setFixedSize(28, 28)
        mic.setCursor(Qt.CursorShape.PointingHandCursor)
        mic.setToolTip("Voice input")
        mic.setIcon(QIcon(_icon_pixmap("mic", 13)))
        mic.setIconSize(QSize(13, 13))
        mic.setStyleSheet(btn_style_ghost)
        mic.clicked.connect(self.mic_clicked.emit)
        lay.addWidget(mic)

        dev = QPushButton("DEV")
        dev.setFixedSize(40, 28)
        dev.setCursor(Qt.CursorShape.PointingHandCursor)
        dev.setToolTip("Developer mode")
        dev.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.06);
                color: rgba(0, 229, 255, 0.7);
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 14px;
                font: 700 8px 'Segoe UI';
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.15);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.5);
            }}
        """)
        dev.clicked.connect(self.developer_clicked.emit)
        lay.addWidget(dev)

        # Send button — golden accent
        send = QPushButton()
        send.setFixedSize(32, 32)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setToolTip("Send")
        send.setIcon(QIcon(_icon_pixmap("send", 14)))
        send.setIconSize(QSize(14, 14))
        send.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.12);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 19px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.25);
                border: 1px solid #00e5ff;
            }}
        """)
        send.clicked.connect(self._submit)
        lay.addWidget(send)

        root.addWidget(frame)

    def show_near(self, anchor: QWidget):
        screen = QApplication.primaryScreen().availableGeometry()
        geo = anchor.geometry()
        x = geo.center().x() - (self.width() // 2)
        y = geo.bottom() + 14
        x = max(screen.left() + 12, min(x, screen.right() - self.width() - 12))
        y = max(screen.top() + 12, min(y, screen.bottom() - self.height() - 12))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        self._input.selectAll()

    def hideEvent(self, event):
        super().hideEvent(event)

    def _submit(self):
        txt = self._input.text().strip()
        if not txt:
            return
        self._input.clear()
        self.submitted.emit(txt)
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def set_audio_level(self, level: float):
        pass

    def set_state(self, state: str):
        pass

    def set_voice_status(self, backend: str = "", live: bool | None = None,
                              detail: str = ""):
        """Forward voice-backend connectivity to the real MainWindow."""
        win = getattr(self, "_win", None)
        if win is not None and hasattr(win, "set_voice_status"):
            try:
                win.set_voice_status(backend, live, detail)
            except Exception:
                pass


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


def _ease_out_expo(x: float) -> float:
    return 1.0 if x == 1.0 else 1.0 - math.pow(2.0, -10.0 * x)

def _ease_in_out_sine(x: float) -> float:
    return -(math.cos(math.pi * x) - 1.0) / 2.0

class ScanningOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        
        self._text = "SCANNING SCREEN"
        self._sub = "Analyzing display..."
        self._result_text = ""
        
        self._time = 0.0
        self._dt = 0.016
        self._fade_out_opacity = 1.0
        self._is_finishing = False
        self._finish_time = 0.0
        
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(16)

        self._particles = []
        self._boxes = [] 
        self._context_lines = []
        
        self.GOLD = QColor("#00E5FF")

    def set_message(self, text: str, sub: str | None = None):
        self._text = (text or "SCANNING SCREEN").upper()
        if sub is not None:
            self._sub = sub
        self.update()

    def show_fullscreen(self, text: str = "SCANNING SCREEN", sub: str = "Analyzing display..."):
        self.set_message(text, sub)
        self._time = 0.0
        self._fade_out_opacity = 1.0
        self._is_finishing = False
        self._finish_time = 0.0
        
        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen else QRectF(0, 0, 1920, 1080).toRect()
        self.setGeometry(geo)
        
        # Capture screen for OpenCV
        if screen:
            pixmap = screen.grabWindow(0)
            self._generate_layout(pixmap)
        else:
            self._generate_layout(None)
        
        self.show()
        self.raise_()
        self.setFocus()

    def hide_overlay(self):
        if not self._is_finishing:
            self._is_finishing = True
            self._finish_time = self._time
            
            self._result_text = "Workspace Recognized\\nVisual Studio Code · Browser · Terminal"

    def force_hide(self):
        self.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.force_hide()
        else:
            super().keyPressEvent(event)

    def _generate_layout(self, pixmap):
        random.seed(42)
        w, h = self.width(), self.height()
        
        # Particles
        self._particles = []
        for _ in range(120):
            self._particles.append({
                'x': random.uniform(0, w),
                'y': random.uniform(0, h),
                'vx': random.uniform(-1, 1),
                'vy': random.uniform(-1, 1),
                'life': random.uniform(0, math.pi * 2)
            })
            
        self._boxes = []
        self._context_lines = []
        
        if pixmap is not None:
            try:
                # Convert QPixmap to CV2
                qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
                width = qimg.width()
                height = qimg.height()
                ptr = qimg.constBits()
                ptr.setsize(height * width * 3)
                arr = np.array(ptr).reshape(height, width, 3)
                
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                edges = cv2.Canny(gray, 50, 150)
                
                kernel = np.ones((5,5), np.uint8)
                dilated = cv2.dilate(edges, kernel, iterations=1)
                
                contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                # Sort contours by area
                contours = sorted(contours, key=cv2.contourArea, reverse=True)
                
                # Take top 30 elements to avoid crowding
                for cnt in contours[:30]:
                    x, y, cw, ch = cv2.boundingRect(cnt)
                    area = cw * ch
                    if area < 150: continue # Skip tiny noise
                    
                    category = "UNKNOWN"
                    start_time = 1.0
                    label = "UI Element"
                    
                    if cw > 400 and ch > 300:
                        category = "WINDOW"
                        start_time = 2.5
                        label = random.choice(["Dashboard Panel", "Task Workspace", "Editor Window"])
                    elif cw > 80 and ch < 40:
                        category = "OCR"
                        start_time = 0.5 + random.uniform(0, 0.5)
                        label = random.choice(["System Online", "Settings", "Microphone", "User Profile"])
                    elif cw < 60 and ch < 60:
                        category = "ICON"
                        start_time = 1.5 + random.uniform(0, 0.5)
                        label = "Icon Node"
                    elif 1.2 < (cw/ch) < 4.0 and ch < 80:
                        category = "BUTTON"
                        start_time = 1.0 + random.uniform(0, 0.5)
                        label = "Action Button"
                    else:
                        continue # Skip weird shapes
                        
                    self._boxes.append({
                        'x': x, 'y': y, 'w': cw, 'h': ch,
                        'category': category,
                        'label': label,
                        'conf': random.randint(67, 98) if category != "WINDOW" else random.randint(95, 99),
                        'start_time': start_time,
                        'has_product': (category == "WINDOW" and random.random() > 0.8)
                    })
                    
            except Exception as e:
                print(f"[ScanningOverlay] CV2 Error: {e}")
                
        # Fallback if no boxes found or CV failed
        if len(self._boxes) == 0:
            for _ in range(5):
                self._boxes.append({
                    'x': random.uniform(w*0.2, w*0.8),
                    'y': random.uniform(h*0.2, h*0.8),
                    'w': random.uniform(100, 300),
                    'h': random.uniform(30, 60),
                    'category': "OCR",
                    'label': "Fallback Element",
                    'conf': 85,
                    'start_time': random.uniform(0.5, 1.5),
                    'has_product': False
                })

        # Generate Context Lines between nearest neighbors (Phase 5)
        for i, b1 in enumerate(self._boxes):
            if i >= 15: break
            for j, b2 in enumerate(self._boxes):
                if i != j and j < 15:
                    dist = math.hypot(b1['x'] - b2['x'], b1['y'] - b2['y'])
                    if dist < 400 and random.random() > 0.7:
                        self._context_lines.append((i, j))
                        break # max 1 connection per node

    def _tick(self):
        self._time += self._dt
        if self._is_finishing:
            dt_finish = self._time - self._finish_time
            if dt_finish > 1.5: 
                self._fade_out_opacity = max(0.0, 1.0 - (dt_finish - 1.5) * 2.0)
                if self._fade_out_opacity <= 0.0:
                    self.hide()
                    return
        if self.isVisible():
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._fade_out_opacity)
        
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(5, 5, 5, 210))
        
        phase1_progress = min(1.0, self._time / 1.5)
        phase6_progress = min(1.0, max(0.0, (self._time - 4.5) / 1.5))
        
        self._draw_particles(p, W, H, phase6_progress)
        self._draw_scan_waves(p, W, H, phase1_progress)
        
        if self._time > 0.5 and not self._is_finishing:
            self._draw_all_boxes(p)
            
        if self._time > 3.5 and not self._is_finishing:
            self._draw_context_lines(p)
            
        if self._time > 4.5 and not self._is_finishing:
            self._draw_neural_net(p, W, H, phase6_progress)
            
        self._draw_center_status(p, W, H)

    def _draw_particles(self, p, W, H, converge_progress):
        cx, cy = W / 2, H / 2
        p.setPen(Qt.PenStyle.NoPen)
        for part in self._particles:
            part['x'] += part['vx']
            part['y'] += part['vy']
            part['life'] += 0.05
            if part['x'] < 0: part['x'] = W
            if part['x'] > W: part['x'] = 0
            if part['y'] < 0: part['y'] = H
            if part['y'] > H: part['y'] = 0
            
            x, y = part['x'], part['y']
            if converge_progress > 0:
                e = _ease_in_out_sine(converge_progress)
                x = x + (cx - x) * e * 0.8
                y = y + (cy - y) * e * 0.8
                
            alpha = int(100 + math.sin(part['life']) * 50)
            p.setBrush(QColor(0, 229, 255, alpha))
            size = 1.5 + math.sin(part['life']) * 1.0
            p.drawEllipse(QRectF(x - size, y - size, size * 2, size * 2))
            
    def _draw_scan_waves(self, p, W, H, progress):
        if progress >= 1.0 or self._is_finishing: return
        e = _ease_out_expo(progress)
        y = e * H
        grad = QLinearGradient(0, y - 50, 0, y + 50)
        grad.setColorAt(0.0, QColor(0, 229, 255, 0))
        grad.setColorAt(0.5, QColor(0, 229, 255, 150))
        grad.setColorAt(1.0, QColor(0, 229, 255, 0))
        p.fillRect(QRectF(0, y - 50, W, 100), grad)
        x = e * W
        grad2 = QLinearGradient(x - 50, 0, x + 50, 0)
        grad2.setColorAt(0.0, QColor(0, 229, 255, 0))
        grad2.setColorAt(0.5, QColor(0, 229, 255, 150))
        grad2.setColorAt(1.0, QColor(0, 229, 255, 0))
        p.fillRect(QRectF(x - 50, 0, 100, H), grad2)
        
    def _draw_all_boxes(self, p):
        p.setBrush(Qt.BrushStyle.NoBrush)
        for box in self._boxes:
            dt = self._time - box['start_time']
            if dt < 0: continue
            
            out_dt = self._time - 4.5
            alpha_mult = max(0.0, 1.0 - out_dt) if out_dt > 0 else 1.0
            if alpha_mult <= 0: continue
            
            prog = min(1.0, dt / 0.8)
            e = _ease_out_expo(prog)
            
            x, y, w, h = box['x'], box['y'], box['w'], box['h']
            cat = box['category']
            
            alpha = int(255 * alpha_mult)
            
            # Styles based on category
            if cat == "OCR":
                br = (w / 4) * e
                p.setPen(QPen(QColor(0, 229, 255, int(150 * alpha_mult)), 1))
                if prog < 0.5:
                    p.drawLine(QPointF(x, y), QPointF(x + br, y))
                    p.drawLine(QPointF(x, y), QPointF(x, y + br))
                    p.drawLine(QPointF(x + w, y), QPointF(x + w - br, y))
                    p.drawLine(QPointF(x + w, y), QPointF(x + w, y + br))
                    p.drawLine(QPointF(x, y + h), QPointF(x + br, y + h))
                    p.drawLine(QPointF(x, y + h), QPointF(x, y + h - br))
                    p.drawLine(QPointF(x + w, y + h), QPointF(x + w - br, y + h))
                    p.drawLine(QPointF(x + w, y + h), QPointF(x + w, y + h - br))
                else:
                    p.drawRect(QRectF(x, y, w, h))
                    
            elif cat == "WINDOW":
                br = 30 * e
                p.setPen(QPen(QColor(0, 229, 255, int(200 * alpha_mult)), 2))
                p.drawLine(QPointF(x, y), QPointF(x + br, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + br))
                p.drawLine(QPointF(x + w, y), QPointF(x + w - br, y))
                p.drawLine(QPointF(x + w, y), QPointF(x + w, y + br))
                p.drawLine(QPointF(x, y + h), QPointF(x + br, y + h))
                p.drawLine(QPointF(x, y + h), QPointF(x, y + h - br))
                p.drawLine(QPointF(x + w, y + h), QPointF(x + w - br, y + h))
                p.drawLine(QPointF(x + w, y + h), QPointF(x + w, y + h - br))
                
            elif cat == "BUTTON":
                p.setPen(QPen(QColor(0, 229, 255, int(100 * e * alpha_mult)), 1))
                p.setBrush(QColor(0, 229, 255, int(20 * e * alpha_mult)))
                p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
                p.setBrush(Qt.BrushStyle.NoBrush)
                
            elif cat == "ICON":
                p.setPen(QPen(QColor(0, 229, 255, int(200 * e * alpha_mult)), 1))
                p.drawEllipse(QRectF(x + w/2 - 4*e, y + h/2 - 4*e, 8*e, 8*e))
                
            if dt > 0.8:
                text_prog = min(1.0, (dt - 0.8) / 0.5)
                te = _ease_out_expo(text_prog)
                
                conf = box['conf']
                label = box['label']
                if conf < 75: label = f"Possible {label}"
                
                p.setFont(QFont("Inter", 8, QFont.Weight.Bold))
                p.setPen(QColor(0, 229, 255, int(255 * te * alpha_mult)))
                ly = y - 6 + (1.0 - te) * 10
                p.drawText(QRectF(x, ly - 10, 200, 12), Qt.AlignmentFlag.AlignLeft, f"{label} {conf}%")

            if box['has_product'] and dt > 2.0:
                card_prog = min(1.0, (dt - 2.0) / 0.5)
                ce = _ease_out_expo(card_prog)
                
                cx, cy = x + w + 20, y + 20
                cw, ch = 220, 90
                
                p.setPen(QPen(QColor(0, 229, 255, int(150 * ce * alpha_mult)), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(x + w, y + 40), QPointF(cx, cy + ch/2))
                
                p.setPen(QPen(QColor(0, 229, 255, int(100 * ce * alpha_mult)), 1))
                p.setBrush(QColor(10, 10, 5, int(220 * ce * alpha_mult)))
                p.drawRoundedRect(QRectF(cx, cy, cw, ch), 6, 6)
                p.setBrush(Qt.BrushStyle.NoBrush)
                
                if card_prog > 0.8:
                    p.setPen(QColor(255, 255, 255, int(255 * alpha_mult)))
                    p.setFont(QFont("Inter", 10, QFont.Weight.Bold))
                    p.drawText(QRectF(cx + 12, cy + 10, cw, 20), Qt.AlignmentFlag.AlignLeft, "Detected Interface")
                    
                    p.setPen(QColor(0, 229, 255, int(255 * alpha_mult)))
                    p.setFont(QFont("Inter", 8))
                    p.drawText(QRectF(cx + 12, cy + 32, cw, 20), Qt.AlignmentFlag.AlignLeft, f"{label} Verified")

    def _draw_context_lines(self, p):
        dt = self._time - 3.5
        out_dt = self._time - 4.5
        alpha_mult = max(0.0, 1.0 - out_dt) if out_dt > 0 else 1.0
        if alpha_mult <= 0: return
        prog = min(1.0, dt / 1.0)
        p.setPen(QPen(QColor(0, 229, 255, int(100 * prog * alpha_mult)), 1, Qt.PenStyle.DotLine))
        for i, j in self._context_lines:
            b1 = self._boxes[i]
            b2 = self._boxes[j]
            c1x, c1y = b1['x'] + b1['w']/2, b1['y'] + b1['h']/2
            c2x, c2y = b2['x'] + b2['w']/2, b2['y'] + b2['h']/2
            path = QPainterPath()
            path.moveTo(c1x, c1y)
            path.cubicTo(c1x + 100, c1y, c2x - 100, c2y, c2x, c2y)
            p.drawPath(path)

    def _draw_neural_net(self, p, W, H, progress):
        cx, cy = W / 2, H / 2
        e = _ease_out_expo(progress)
        r = 80 * e
        grad = QLinearGradient(cx - r, cy - r, cx + r, cy + r)
        grad.setColorAt(0.0, QColor(0, 229, 255, 150))
        grad.setColorAt(1.0, QColor(0, 229, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QRectF(cx - r*1.5, cy - r*1.5, r*3, r*3))
        p.setPen(QPen(QColor(0, 229, 255, int(200 * e)), 1))
        num_nodes = 8
        time_rot = self._time * 0.5
        nodes = []
        for i in range(num_nodes):
            angle = (i / num_nodes) * math.pi * 2 + time_rot
            orbit_r = 120 + math.sin(self._time * 2 + i) * 20
            nx = cx + math.cos(angle) * orbit_r * e
            ny = cy + math.sin(angle) * orbit_r * e
            nodes.append((nx, ny))
            p.setBrush(QColor(0, 229, 255, 255))
            p.drawEllipse(QRectF(nx - 3, ny - 3, 6, 6))
        for i in range(num_nodes):
            n1 = nodes[i]
            n2 = nodes[(i+1)%num_nodes]
            n3 = nodes[(i+3)%num_nodes]
            p.drawLine(QPointF(*n1), QPointF(*n2))
            p.drawLine(QPointF(*n1), QPointF(*n3))
            p.drawLine(QPointF(cx, cy), QPointF(*n1))

    def _draw_center_status(self, p, W, H):
        cx, cy = W / 2, H / 2
        if self._is_finishing:
            dt = self._time - self._finish_time
            prog = min(1.0, dt / 0.5)
            e = _ease_out_expo(prog)
            pw, ph = 400, 120
            p.setPen(QPen(QColor(0, 229, 255, int(150 * e)), 1))
            p.setBrush(QColor(10, 10, 5, int(230 * e)))
            p.drawRoundedRect(QRectF(cx - pw/2, cy - ph/2, pw, ph), 8, 8)
            if prog > 0.8:
                p.setPen(QColor(255, 255, 255, 255))
                p.setFont(QFont("Inter", 14, QFont.Weight.Bold))
                lines = self._result_text.split("\\n")
                if len(lines) > 0:
                    p.drawText(QRectF(cx - pw/2, cy - 20, pw, 24), Qt.AlignmentFlag.AlignCenter, lines[0])
                if len(lines) > 1:
                    p.setPen(QColor(0, 229, 255, 255))
                    p.setFont(QFont("Inter", 10))
                    p.drawText(QRectF(cx - pw/2, cy + 10, pw, 20), Qt.AlignmentFlag.AlignCenter, lines[1])
        else:
            status = "INITIALIZING..."
            if self._time > 1.0: status = "SCANNING OCR..."
            if self._time > 2.5: status = "RECOGNIZING OBJECTS..."
            if self._time > 3.5: status = "ANALYZING CONTEXT..."
            if self._time > 4.5:
                cycle = int(self._time * 2) % 4
                dots = "." * cycle
                status = f"THINKING{dots}"
            p.setPen(QColor(255, 255, 255, 255))
            p.setFont(QFont("Inter", 12, QFont.Weight.Bold))
            p.drawText(QRectF(0, cy + 160, W, 20), Qt.AlignmentFlag.AlignCenter, status)


class BootSequenceOverlay(QWidget):
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowOpacity(1.0)

        self._boot_finished = False
        self._time = 0.0
        self._dt = 0.016
        self._total_duration = 3.6

        # Particles & Effects State
        self._particles = []
        self._impact_sparks = []
        self._played_whoosh = False
        self._played_impact = False

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)

    def _init_particles(self, w: float, h: float):
        self._particles = []
        for _ in range(280):
            self._particles.append({
                'x': random.uniform(0, w),
                'y': random.uniform(0, h),
                'vx': random.uniform(-0.65, 0.65),
                'vy': random.uniform(-0.65, 0.65),
                'size': random.uniform(1.4, 4.0),
                'base_alpha': random.uniform(0.65, 1.0),
                'twinkle_speed': random.uniform(2.5, 6.5),
                'twinkle_phase': random.uniform(0, math.pi * 2),
                'converge_delay': random.uniform(0.0, 0.25),
                'orig_x': 0.0,
                'orig_y': 0.0,
            })

    def start(self, device_name: str = "", greeting_name: str = ""):
        self._boot_finished = False
        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen else QRectF(0, 0, 1920, 1080).toRect()
        self.setGeometry(geo)
        self._init_particles(float(geo.width()), float(geo.height()))
        self._time = 0.0
        self._played_whoosh = False
        self._played_impact = False
        self.show()
        self.raise_()
        self.activateWindow()
        self._tmr.start(16)

    def add_step(self, text: str):
        pass

    def set_step_status(self, text: str, status: str):
        pass

    def set_progress(self, percent: int, tip: str | None = None):
        pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._tmr.stop()
            self._boot_finished = True
            self.hide()
            self.close()
            self.finished.emit()

    def _tick(self):
        self._time += self._dt
        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = h / 2.0

        # Phase 1 (0.0 to 1.1s): particles drift across full screen
        if self._time < 1.1:
            for p in self._particles:
                p['x'] += p['vx']
                p['y'] += p['vy']
                if p['x'] < 0: p['x'] = w
                elif p['x'] > w: p['x'] = 0
                if p['y'] < 0: p['y'] = h
                elif p['y'] > h: p['y'] = 0

        # Phase 2 (1.1s to 1.8s): burst & convergence towards center
        elif 1.1 <= self._time < 1.8:
            if not self._played_whoosh:
                self._played_whoosh = True
                try:
                    from sound_manager import SoundManager
                    SoundManager.instance().play_deploy_whoosh()
                except Exception:
                    pass
                for p in self._particles:
                    p['orig_x'] = p['x']
                    p['orig_y'] = p['y']

            burst_t = (self._time - 1.1) / 0.7
            for p in self._particles:
                local_t = max(0.0, min(1.0, (burst_t - p['converge_delay']) / (1.0 - p['converge_delay'] + 0.001)))
                ease = math.pow(local_t, 3.2)
                p['x'] = p['orig_x'] + (cx - p['orig_x']) * ease
                p['y'] = p['orig_y'] + (cy - 30.0 - p['orig_y']) * ease

        # Phase 4 (2.5s to 3.2s): AI - EVO entrance with impact
        if self._time >= 2.5 and not self._played_impact:
            self._played_impact = True
            try:
                from sound_manager import SoundManager
                SoundManager.instance().play_mission_complete()
            except Exception:
                pass
            self._impact_sparks = []
            for _ in range(70):
                angle = random.uniform(0, math.pi * 2)
                spd = random.uniform(6.0, 24.0)
                self._impact_sparks.append({
                    'x': cx,
                    'y': cy + 55.0,
                    'vx': math.cos(angle) * spd,
                    'vy': math.sin(angle) * spd * 0.65,
                    'alpha': 1.0,
                    'size': random.uniform(2.0, 4.5),
                    'color': random.choice(['#00f0ff', '#ffffff', '#7dd3fc', '#38bdf8'])
                })

        if self._impact_sparks:
            for s in self._impact_sparks:
                s['x'] += s['vx']
                s['y'] += s['vy']
                s['vx'] *= 0.92
                s['vy'] *= 0.92
                s['alpha'] *= 0.92

        self.update()

        if self._time >= self._total_duration:
            self._tmr.stop()
            self._boot_finished = True
            self.hide()
            self.close()
            self.finished.emit()

    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            # Minimal DWM compositing fill (alpha=1 ensures DWM layered composition without visible darkening)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

            w = float(self.width())
            h = float(self.height())
            cx = w / 2.0
            cy = h / 2.0

            # DO NOT DRAW BACKGROUND FILL: Purely transparent!

            # Transition out alpha (Phase 5: 3.2s to 3.6s)
            global_alpha = 1.0
            if self._time >= 3.2:
                exit_t = min(1.0, (self._time - 3.2) / 0.4)
                global_alpha = max(0.0, 1.0 - exit_t)

            # Camera Shake calculation during impact (2.5s to 3.0s)
            shake_x, shake_y = 0.0, 0.0
            if 2.5 <= self._time < 3.0:
                shake_dt = self._time - 2.5
                decay = max(0.0, 1.0 - (shake_dt / 0.45))
                shake_x = math.sin(shake_dt * 65.0) * 16.0 * decay
                shake_y = math.cos(shake_dt * 52.0) * 10.0 * decay

            painter.translate(shake_x, shake_y)

            # -------------------------------------------------------------
            # 1. DRAW PARTICLES
            # -------------------------------------------------------------
            if self._time < 1.8:
                for p in self._particles:
                    p_alpha = p['base_alpha']
                    if self._time < 0.6:
                        p_alpha *= (self._time / 0.6)
                    twinkle = 0.75 + 0.25 * math.sin(self._time * p['twinkle_speed'] + p['twinkle_phase'])
                    final_alpha = int(255 * p_alpha * twinkle * global_alpha)

                    if final_alpha <= 0:
                        continue

                    if p['size'] > 2.5:
                        glow_pen = QColor(255, 255, 255, int(final_alpha * 0.25))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(glow_pen)
                        painter.drawEllipse(QPointF(p['x'], p['y']), p['size'] * 2.2, p['size'] * 2.2)

                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(255, 255, 255, final_alpha))
                    painter.drawEllipse(QPointF(p['x'], p['y']), p['size'], p['size'])

            # -------------------------------------------------------------
            # 2. DRAW CENTER CONVERGENCE FLASH & SHOCKWAVE
            # -------------------------------------------------------------
            if 1.4 <= self._time < 1.9:
                flash_t = (self._time - 1.4) / 0.5
                flash_rad = 12.0 + math.sin(flash_t * math.pi) * 80.0
                flash_alpha = int(255 * math.sin(flash_t * math.pi) * global_alpha)
                if flash_alpha > 0:
                    grad = QRadialGradient(cx, cy - 30.0, flash_rad)
                    grad.setColorAt(0.0, QColor(255, 255, 255, flash_alpha))
                    grad.setColorAt(0.3, QColor(0, 240, 255, int(flash_alpha * 0.7)))
                    grad.setColorAt(1.0, QColor(0, 240, 255, 0))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(grad))
                    painter.drawEllipse(QPointF(cx, cy - 30.0), flash_rad, flash_rad)

            # Impact Shockwave at 2.5s
            if self._time >= 2.5:
                sw_prog = min(1.0, (self._time - 2.5) / 0.6)
                sw_rad = sw_prog * 480.0
                sw_alpha = int(255 * (1.0 - sw_prog) * global_alpha)
                if sw_alpha > 0:
                    sw_pen = QPen(QColor(0, 240, 255, sw_alpha), 3.0)
                    painter.setPen(sw_pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(cx, cy + 55.0), sw_rad, sw_rad * 0.6)

            # Impact Sparks
            if self._impact_sparks:
                painter.setPen(Qt.PenStyle.NoPen)
                for s in self._impact_sparks:
                    s_alpha = int(255 * s['alpha'] * global_alpha)
                    if s_alpha <= 0: continue
                    col = QColor(s['color'])
                    col.setAlpha(s_alpha)
                    painter.setBrush(col)
                    painter.drawEllipse(QPointF(s['x'], s['y']), s['size'], s['size'])

            # -------------------------------------------------------------
            # 3. DRAW "BRAHMA" TEXT & TYPOGRAPHY EFFECT
            # -------------------------------------------------------------
            if self._time >= 1.35:
                text_t = min(1.0, (self._time - 1.35) / 0.45)
                text_alpha = int(255 * text_t * global_alpha)

                spacing_prog = min(1.0, (self._time - 1.35) / 1.5)
                letter_spacing = 10.0 + (spacing_prog * 14.0)

                font_brahma = QFont("Segoe UI", 56, QFont.Weight.Black)
                font_brahma.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
                painter.setFont(font_brahma)

                rect_brahma = QRectF(cx - 500, cy - 85, 1000, 90)

                # Outer text cyan glow
                glow_col = QColor(0, 240, 255, int(text_alpha * 0.45))
                painter.setPen(glow_col)
                for ox, oy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)]:
                    painter.drawText(rect_brahma.translated(ox, oy), Qt.AlignmentFlag.AlignCenter, "BRAHMA")

                # Core white text with dynamic shimmer
                if 1.8 <= self._time < 2.5:
                    shim_t = (self._time - 1.8) / 0.7
                    sweep_x = cx - 350 + (shim_t * 700)
                    grad = QLinearGradient(sweep_x - 120, cy, sweep_x + 120, cy)
                    grad.setColorAt(0.0, QColor(255, 255, 255, int(text_alpha * 0.75)))
                    grad.setColorAt(0.5, QColor(0, 240, 255, text_alpha))
                    grad.setColorAt(0.7, QColor(255, 255, 255, text_alpha))
                    grad.setColorAt(1.0, QColor(255, 255, 255, int(text_alpha * 0.75)))
                    painter.setPen(QPen(QBrush(grad), 1))
                else:
                    painter.setPen(QColor(255, 255, 255, text_alpha))

                painter.drawText(rect_brahma, Qt.AlignmentFlag.AlignCenter, "BRAHMA")

            # -------------------------------------------------------------
            # 4. DRAW "AI - EVO" WITH MAXIMUM IMPACT (>= 2.5s)
            # -------------------------------------------------------------
            if self._time >= 2.5:
                evo_dt = self._time - 2.5
                evo_alpha = int(255 * min(1.0, evo_dt / 0.08) * global_alpha)

                slam_scale = 1.0
                if evo_dt < 0.18:
                    slam_t = evo_dt / 0.18
                    slam_scale = 1.5 - (0.5 * math.sin(slam_t * math.pi * 0.5))

                painter.save()
                painter.translate(cx, cy + 50.0)
                painter.scale(slam_scale, slam_scale)

                badge_w = 260.0
                badge_h = 44.0
                badge_rect = QRectF(-badge_w / 2.0, -badge_h / 2.0, badge_w, badge_h)

                painter.setPen(QPen(QColor(0, 240, 255, int(evo_alpha * 0.8)), 1.5))
                painter.setBrush(QColor(0, 20, 35, int(evo_alpha * 0.55)))
                painter.drawRoundedRect(badge_rect, 22.0, 22.0)

                font_sub = QFont("Segoe UI", 18, QFont.Weight.Bold)
                font_sub.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4.0)
                painter.setFont(font_sub)

                painter.setPen(QColor(220, 230, 245, evo_alpha))
                painter.drawText(QRectF(-badge_w / 2.0, -badge_h / 2.0, 125, badge_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "AI - ")

                font_evo = QFont("Segoe UI", 20, QFont.Weight.Black)
                font_evo.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 5.0)
                painter.setFont(font_evo)

                evo_glow = QColor(0, 240, 255, int(evo_alpha * 0.6))
                painter.setPen(evo_glow)
                for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    painter.drawText(QRectF(-badge_w / 2.0 + 130, -badge_h / 2.0, 115, badge_h).translated(ox, oy), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "EVO")

                painter.setPen(QColor(0, 240, 255, evo_alpha))
                painter.drawText(QRectF(-badge_w / 2.0 + 130, -badge_h / 2.0, 115, badge_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "EVO")

                painter.restore()

            painter.end()
        except Exception as e:
            import traceback
            traceback.print_exc()


class IncomingAlertDialog(QDialog):
    decision = pyqtSignal(str)

    def __init__(self, event: dict, parent=None):
        super().__init__(parent)
        self._event = event or {}
        self._kind = (self._event.get("kind") or "message").strip().lower()
        self._app = (self._event.get("app") or "App").strip()
        self._title = (self._event.get("title") or "").strip()
        self._preview = (self._event.get("preview") or "").strip()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("IncomingAlertDialog")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("IncomingAlertFrame")
        frame.setStyleSheet(f"""
            QFrame#IncomingAlertFrame {{
                background: rgba(8, 8, 8, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 16px;
            }}
        """)
        root.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        heading = QLabel("Incoming Call" if self._kind == "call" else "Incoming Message")
        heading.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        heading.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(heading)

        app_lbl = QLabel(f"From {self._app}")
        app_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        app_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(app_lbl)

        body = self._preview or self._title or "A notification was detected."
        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setFont(QFont("Segoe UI", 10))
        body_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(body_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def _btn(text: str, *, primary: bool = False, danger: bool = False) -> QPushButton:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            fg = C.WHITE
            border = C.BORDER_B if primary else C.BORDER
            bg = "rgba(255,255,255,0.10)" if primary else "rgba(14,14,14,235)"
            if danger:
                border = C.RED
                bg = "rgba(60,10,10,235)"
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {bg};
                    color: {fg};
                    border: 1px solid {border};
                    border-radius: 11px;
                    padding: 0 12px;
                }}
                QPushButton:hover {{
                    background: rgba(255,255,255,0.14);
                    border: 1px solid {C.WHITE};
                }}
            """)
            return btn

        if self._kind == "call":
            self._accept_btn = _btn("Pick up", primary=True)
            self._ignore_btn = _btn("Ignore")
            self._cut_btn = _btn("Cut call", danger=True)
            self._x_btn = _btn("X")
            self._accept_btn.clicked.connect(lambda: self._choose("accept"))
            self._ignore_btn.clicked.connect(lambda: self._choose("ignore"))
            self._cut_btn.clicked.connect(lambda: self._choose("cut"))
            self._x_btn.clicked.connect(lambda: self._choose("noop"))
            for btn in (self._accept_btn, self._ignore_btn, self._cut_btn, self._x_btn):
                btn_row.addWidget(btn)
        else:
            self._hear_btn = _btn("Hear it", primary=True)
            self._reply_btn = _btn("Reply")
            self._ignore_btn = _btn("Ignore")
            self._x_btn = _btn("X")
            self._hear_btn.clicked.connect(lambda: self._choose("hear"))
            self._reply_btn.clicked.connect(lambda: self._choose("reply"))
            self._ignore_btn.clicked.connect(lambda: self._choose("ignore"))
            self._x_btn.clicked.connect(lambda: self._choose("noop"))
            btn_row.addWidget(self._hear_btn)
            btn_row.addWidget(self._reply_btn)
            btn_row.addWidget(self._ignore_btn)
            btn_row.addWidget(self._x_btn)

        lay.addLayout(btn_row)

    def _choose(self, decision: str):
        self.decision.emit(decision)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._choose("ignore")
            return
        super().keyPressEvent(event)


class MeetingOverlay(QWidget):
    stop_requested = pyqtSignal()
    minimize_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self._expanded_height = 142
        self._collapsed_height = 58
        self._collapsed = False
        self.setFixedHeight(self._expanded_height)
        self.setStyleSheet("background: transparent;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(5, 5, 5, 232);
                border: 1px solid {C.BORDER_B};
                border-radius: 18px;
            }}
        """)
        root.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        self._badge = QLabel("MEETING MODE")
        self._badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._badge.setStyleSheet(
            f"color: {C.WHITE}; background: rgba(255,255,255,0.06); border: 1px solid {C.BORDER_B}; border-radius: 10px; padding: 4px 10px;"
        )
        top.addWidget(self._badge)

        self._title = QLabel("Watching the meeting")
        self._title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        top.addWidget(self._title)
        top.addStretch()

        self._min_btn = QPushButton("-")
        self._min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_btn.setFixedSize(28, 28)
        self._min_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._min_btn.setToolTip("Minimize meeting bar")
        self._min_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 9px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border: 1px solid {C.WHITE};
            }}
        """)
        self._min_btn.clicked.connect(self._toggle_collapsed)
        top.addWidget(self._min_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setFixedHeight(28)
        self._stop_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 9px;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border: 1px solid {C.WHITE};
            }}
        """)
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        top.addWidget(self._stop_btn)

        self._close_btn = QPushButton("x")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._close_btn.setToolTip("Close meeting bar")
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {C.WHITE};
                border: 1px solid {C.BORDER_B};
                border-radius: 9px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.10);
                border: 1px solid {C.WHITE};
            }}
        """)
        self._close_btn.clicked.connect(self.close_requested.emit)
        top.addWidget(self._close_btn)
        lay.addLayout(top)

        self._summary = QLabel("Waiting for a meeting to start...")
        self._summary.setWordWrap(True)
        self._summary.setFont(QFont("Segoe UI", 10))
        self._summary.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._summary)

        self._speech = QLabel("They said: nothing yet.")
        self._speech.setWordWrap(True)
        self._speech.setFont(QFont("Segoe UI", 10))
        self._speech.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._speech)

        self._answer = QLabel("Brahma Evo will show the live answer here.")
        self._answer.setWordWrap(True)
        self._answer.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._answer.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._answer)

        self._apply_collapsed_state(False)

    def set_content(self, title: str, summary: str, answer: str, active: bool = True, speech: str = ""):
        self._title.setText(title or "Watching the meeting")
        self._summary.setText(summary or "Watching the meeting screen.")
        self._speech.setText(f"They said: {speech or 'nothing yet.'}")
        self._answer.setText(answer or "No question detected yet.")
        self._badge.setText("MEETING LIVE" if active else "MEETING MODE")

    def _apply_collapsed_state(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        for widget in (self._summary, self._speech, self._answer):
            widget.setVisible(not self._collapsed)
        self._min_btn.setText("?" if self._collapsed else "-")
        self._min_btn.setToolTip("Restore meeting bar" if self._collapsed else "Minimize meeting bar")
        self.setFixedHeight(self._collapsed_height if self._collapsed else self._expanded_height)

    def set_collapsed(self, collapsed: bool):
        self._apply_collapsed_state(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _toggle_collapsed(self):
        self.minimize_requested.emit()


class FloatingLauncher(QWidget):
    single_clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    action_requested = pyqtSignal(str)
    position_changed = pyqtSignal(int, int)

    _STATE_THEMES = {
        "idle": (255, 255, 255),       # Luminous Pure White
        "listening": (0, 229, 255),    # Electric Ice Cyan
        "thinking": (160, 230, 255),   # Frost Blue
        "speaking": (255, 255, 255),   # Brilliant White
        "executing": (0, 229, 255),    # Dynamic Ice Cyan
        "processing": (0, 229, 255),   # Dynamic Ice Cyan
        "working": (0, 229, 255),      # Dynamic Ice Cyan
        "muted": (160, 160, 160),      # Cool Silver
        "standby": (110, 122, 138),    # Dormant Slate - session down
        "error": (255, 82, 82),        # Alert Crimson
    }

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(88, 88)
        self._state = "idle"
        self._status_line = "Ready"
        self._hovered = False

        # Animation state
        self._anim_angle = 0.0
        self._ring2_angle = 0.0
        self._breath_val = 0.0
        self._breath_dir = 1
        self._particle_angles = [i * 45.0 for i in range(8)]
        self._particle_trails = [[] for _ in range(8)]

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_timer.start(25)  # 40fps

        self._single_timer = QTimer(self)
        self._single_timer.setSingleShot(True)
        self._single_timer.timeout.connect(self._on_single_click_timeout)

        # Kinetic spring physics & audio reactivity
        self._audio_lvl = 0.0
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        self._target_snap_x = 0
        self._spring_timer = QTimer(self)
        self._spring_timer.setInterval(16)
        self._spring_timer.timeout.connect(self._spring_step)

        self._dragging = False
        self._drag_button = None
        self._drag_offset = QPoint(0, 0)
        self._press_pos = QPoint(0, 0)
        self._apply_state_style()

    def set_audio_level(self, lvl: float):
        self._audio_lvl = max(0.0, min(1.0, float(lvl)))
        self.update()

    def _spring_step(self):
        curr_x = self.x()
        diff = self._target_snap_x - curr_x
        if abs(diff) < 2:
            self.move(self._target_snap_x, self.y())
            self._spring_timer.stop()
            self.position_changed.emit(self.x(), self.y())
        else:
            step = int(diff * 0.28)
            if step == 0:
                step = 1 if diff > 0 else -1
            self.move(curr_x + step, self.y())

    def _on_single_click_timeout(self):
        self.single_clicked.emit()

    def hideEvent(self, event):
        super().hideEvent(event)
        if hasattr(self, "_anim_timer") and self._anim_timer.isActive():
            self._anim_timer.stop()
        if hasattr(self, "_spring_timer") and self._spring_timer.isActive():
            self._spring_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_anim_timer") and not self._anim_timer.isActive():
            self._anim_timer.start(25)

    def _anim_tick(self):
        if not self.isVisible():
            if hasattr(self, "_anim_timer") and self._anim_timer.isActive():
                self._anim_timer.stop()
            return
        import math
        # State-dependent spin speeds
        spin_speed = 3.2 if self._state == "thinking" else (2.4 if self._state in ("listening", "speaking") else 1.5)
        self._anim_angle = (self._anim_angle + spin_speed) % 360.0
        self._ring2_angle = (self._ring2_angle - spin_speed * 0.75) % 360.0

        breath_step = 0.04 if self._state in ("listening", "speaking", "thinking") else 0.022
        self._breath_val += breath_step * self._breath_dir
        if self._breath_val >= 1.0:
            self._breath_val = 1.0
            self._breath_dir = -1
        elif self._breath_val <= 0.0:
            self._breath_val = 0.0
            self._breath_dir = 1

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        for i in range(len(self._particle_angles)):
            speed = 0.9 + i * 0.14
            self._particle_angles[i] = (self._particle_angles[i] + speed) % 360.0
            rad = math.radians(self._particle_angles[i])
            orbit_r = 35.0 + (i % 3) * 2.2 + self._audio_lvl * 5.0
            px = cx + math.cos(rad) * orbit_r
            py = cy + math.sin(rad) * orbit_r
            trail = self._particle_trails[i]
            trail.append((px, py))
            if len(trail) > 5:
                trail.pop(0)
        self.update()

    def paintEvent(self, event):
        import math
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        ar, ag, ab = self._STATE_THEMES.get(self._state, (255, 255, 255))
        breath = self._breath_val
        hover_boost = 1.35 if self._hovered else 1.0
        audio_boost = self._audio_lvl * 12.0

        # ── 1.5 Dynamic Acoustic Shockwave Ring (Voice Reactive) ──
        if self._audio_lvl > 0.05:
            shock_r = 38.0 + breath * 4.0 + self._audio_lvl * 20.0
            shock_alpha = int(min(220, self._audio_lvl * 240))
            shock_pen = QPen(QColor(0, 229, 255, shock_alpha), 1.8)
            painter.setPen(shock_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), shock_r, shock_r)

        # ── 1. Pulsing Ambient Glow (Audio Reactive) ──
        glow_r = (38.0 + breath * 5.5 + audio_boost) * (1.1 if self._hovered else 1.0)
        glow_alpha = int((30 + breath * 45 + self._audio_lvl * 70) * hover_boost)
        glow = QRadialGradient(cx, cy, glow_r)
        glow.setColorAt(0.0, QColor(ar, ag, ab, min(255, glow_alpha)))
        glow.setColorAt(0.55, QColor(ar, ag, ab, min(255, int(glow_alpha * 0.35))))
        glow.setColorAt(1.0, QColor(ar, ag, ab, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # ── 2. Subtle Outer Orbit Guide Ring ──
        guide_pen = QPen(QColor(ar, ag, ab, int(25 + breath * 20)), 0.75)
        guide_pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(guide_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 39.0, 39.0)

        # ── 3. High-Tech Dual Rotating Arcs ──
        # Primary Outer Arc
        a1 = min(255, int((170 + breath * 75 + self._audio_lvl * 50) * hover_boost))
        pen1 = QPen(QColor(ar, ag, ab, a1), 2.2 + self._audio_lvl * 1.5)
        pen1.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen1)
        rect1 = QRectF(cx - 35.0, cy - 35.0, 70.0, 70.0)
        arc_len = 110 if self._state != "thinking" else 140
        painter.drawArc(rect1, int(self._anim_angle * 16), int(arc_len * 16))

        # Secondary Counter-Rotating Arc
        a2 = min(255, int((90 + breath * 50) * hover_boost))
        pen2 = QPen(QColor(ar, ag, ab, a2), 1.3)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen2)
        rect2 = QRectF(cx - 31.5, cy - 31.5, 63.0, 63.0)
        painter.drawArc(rect2, int(self._ring2_angle * 16), int(80 * 16))

        # Orbiting Satellite Beacon
        sat_rad = math.radians(self._anim_angle + 180.0)
        sat_x = cx + math.cos(sat_rad) * 35.0
        sat_y = cy + math.sin(sat_rad) * 35.0
        sat_color = QColor(ar, ag, ab).lighter(150)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(sat_color.red(), sat_color.green(), sat_color.blue(), min(255, int(180 * hover_boost)))))
        painter.drawEllipse(QPointF(sat_x, sat_y), 2.2, 2.2)

        # ── 4. Central Glassmorphic Core (with Cursor Parallax) ──
        gx, gy = self._gaze_x, self._gaze_y
        core_r = 25.5 + self._audio_lvl * 2.0
        core_grad = QRadialGradient(cx + gx * 0.5, cy - 4.0 + gy * 0.5, core_r)
        core_grad.setColorAt(0.0, QColor(20, 22, 30, 246))
        core_grad.setColorAt(0.85, QColor(10, 11, 16, 252))
        core_grad.setColorAt(1.0, QColor(ar, ag, ab, 30))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(core_grad))
        painter.drawEllipse(QPointF(cx + gx * 0.5, cy + gy * 0.5), core_r, core_r)

        # Glowing Core Rim
        rim_alpha = min(255, int((110 + breath * 80 + self._audio_lvl * 60) * hover_boost))
        rim_pen = QPen(QColor(ar, ag, ab, rim_alpha), 1.2)
        painter.setPen(rim_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx + gx * 0.5, cy + gy * 0.5), core_r, core_r)

        # ── 5. Orbiting Micro-Particle Comet Trails ──
        for i in range(len(self._particle_trails)):
            trail = self._particle_trails[i]
            for t_idx, (px, py) in enumerate(trail):
                frac = (t_idx + 1) / max(len(trail), 1)
                t_alpha = min(255, int(frac * (50 + breath * 70) * hover_boost))
                t_size = 0.5 + frac * 1.2
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(ar, ag, ab, t_alpha)))
                painter.drawEllipse(QPointF(px, py), t_size, t_size)
            if trail:
                hx, hy = trail[-1]
                head_alpha = min(255, int((120 + breath * 120) * hover_boost))
                head_c = QColor(ar, ag, ab).lighter(140)
                painter.setBrush(QBrush(QColor(head_c.red(), head_c.green(), head_c.blue(), head_alpha)))
                painter.drawEllipse(QPointF(hx, hy), 1.6, 1.6)

        # ── 6. Emblem: Hindi "ब्रह्मा" + Status Dot with Parallax ──
        text_alpha = min(255, int((215 + breath * 40) * hover_boost))
        painter.setPen(QPen(QColor(0, 0, 0, 180)))
        painter.setFont(QFont("Nirmala UI", 11, QFont.Weight.Bold))
        painter.drawText(QRectF(cx - 21.0 + gx, cy - 13.0 + gy, 44.0, 22.0), Qt.AlignmentFlag.AlignCenter, "\u092C\u094D\u0930\u0939\u094D\u092E\u093E")

        # Crisp glowing text
        painter.setPen(QPen(QColor(ar, ag, ab, text_alpha)))
        painter.setFont(QFont("Nirmala UI", 11, QFont.Weight.Bold))
        painter.drawText(QRectF(cx - 22.0 + gx, cy - 14.0 + gy, 44.0, 22.0), Qt.AlignmentFlag.AlignCenter, "\u092C\u094D\u0930\u0939\u094D\u092E\u093E")

        # Tiny breathing status beacon directly below text
        dot_alpha = min(255, int((150 + breath * 105) * hover_boost))
        dot_color = QColor(ar, ag, ab, dot_alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot_color))
        painter.drawEllipse(QPointF(cx + gx, cy + 12.0 + gy), 2.0, 2.0)

        painter.end()

    def show_at(self, x: int | None = None, y: int | None = None):
        if x is None or y is None:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 18
            y = screen.bottom() - self.height() - 90
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def set_state(self, state: str, detail: str | None = None):
        old_state = self._state
        self._state = (state or "idle").strip().lower()
        self._status_line = (detail or self._default_status()).strip() or self._default_status()
        self._apply_state_style()

        # Acoustic cyber cues on state changes
        if self._state != old_state:
            try:
                from sound_manager import SoundManager
                sm = SoundManager.instance()
                if self._state == "listening":
                    sm.play_listening_start()
                elif self._state == "idle" and old_state in ("thinking", "executing", "processing"):
                    sm.play_mission_complete()
                elif self._state in ("thinking", "executing", "processing"):
                    sm.play_telemetry_chirp()
            except Exception:
                pass

    def _default_status(self) -> str:
        return {
            "idle": "Ready",
            "listening": "Listening",
            "thinking": "Thinking...",
            "speaking": "Speaking",
            "executing": "Executing task...",
            "processing": "Processing...",
            "muted": "Muted",
            # Not On-Call-specific: standby is also what an explicit Disconnect
            # looks like in Always On, on either voice backend.
            "standby": "Standby - no session",
            "error": "Error",
        }.get(self._state, "Ready")

    def _apply_state_style(self):
        self.setToolTip(
            f"Brahma Evo ({self._status_line})\n"
            "• Single-click: Chat Workspace\n"
            "• Double-click: Open Full App\n"
            "• Drag: Move (Spring Snap)"
        )
        self.update()

    def _show_menu(self, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: rgba(14, 16, 22, 248);
                color: #FFFFFF;
                border: 1px solid rgba(0, 229, 255, 0.45);
                border-radius: 12px;
                padding: 6px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 7px 18px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: rgba(0, 229, 255, 0.20);
                color: #00e5ff;
            }}
            QMenu::separator {{
                height: 1px;
                background: rgba(255, 255, 255, 0.10);
                margin: 4px 6px;
            }}
        """)

        open_full = QAction("Open Brahma Evo (Full App)", self)
        open_full.triggered.connect(lambda: self.action_requested.emit("open_app"))
        menu.addAction(open_full)

        open_ws = QAction("Open Chat Workspace", self)
        open_ws.triggered.connect(lambda: self.action_requested.emit("open_workspace"))
        menu.addAction(open_ws)

        menu.addSeparator()

        voice_mode = QAction("Toggle Voice Mode", self)
        voice_mode.triggered.connect(lambda: self.action_requested.emit("voice_mode"))
        menu.addAction(voice_mode)

        settings_act = QAction("Settings", self)
        settings_act.triggered.connect(lambda: self.action_requested.emit("settings"))
        menu.addAction(settings_act)

        menu.addSeparator()

        hide_act = QAction("Hide Floating Icon", self)
        hide_act.triggered.connect(self.hide)
        menu.addAction(hide_act)

        quit_act = QAction("Quit Brahma", self)
        quit_act.triggered.connect(lambda: self.action_requested.emit("quit"))
        menu.addAction(quit_act)

        menu.exec(global_pos)

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            if hasattr(self, "_spring_timer") and self._spring_timer.isActive():
                self._spring_timer.stop()
            self._dragging = False
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._single_timer.stop()
            self._drag_button = event.button()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.globalPosition().toPoint()
        # Calculate subtle cursor parallax
        local_x = pos.x() - self.x() - self.width() / 2.0
        local_y = pos.y() - self.y() - self.height() / 2.0
        self._gaze_x = max(-2.5, min(2.5, local_x * 0.05))
        self._gaze_y = max(-2.5, min(2.5, local_y * 0.05))

        if event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton):
            if not self._dragging and (pos - self._press_pos).manhattanLength() > 5:
                self._dragging = True
            if self._dragging:
                screen = QApplication.primaryScreen().availableGeometry()
                new_pos = pos - self._drag_offset
                new_x = max(screen.left(), min(new_pos.x(), screen.right() - self.width()))
                new_y = max(screen.top(), min(new_pos.y(), screen.bottom() - self.height()))
                self.move(new_x, new_y)
                self.position_changed.emit(new_x, new_y)
            event.accept()
            return
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._dragging:
                self._single_timer.start(200)
            else:
                # Magnetic Spring Edge-Snap
                screen = QApplication.primaryScreen().availableGeometry()
                mid_x = (screen.left() + screen.right()) / 2.0
                if self.x() < mid_x:
                    self._target_snap_x = screen.left() + 16
                else:
                    self._target_snap_x = screen.right() - self.width() - 16
                self._spring_timer.start(16)
            self._dragging = False
            self._drag_button = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if not self._dragging:
                self._show_menu(event.globalPosition().toPoint())
            self._dragging = False
            self._drag_button = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_timer.stop()
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._apply_state_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._gaze_x = 0.0
        self._gaze_y = 0.0
        self._apply_state_style()
        super().leaveEvent(event)



class FloatingGestureCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        
        self.btn = QPushButton("🖐 Gestures")
        self.btn.setFixedSize(100, 36)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setStyleSheet(
            "QPushButton { background: #0c0e12; color: #ffffff; border: 1px solid rgba(0, 229, 255, 0.20); border-radius: 8px; font-weight: bold; }"
            "QPushButton:hover { color: #00e5ff; border: 1px solid #00e5ff; }"
        )
        self.btn.clicked.connect(self.toggle_preview)
        
        btn_lay = QHBoxLayout()
        btn_lay.addStretch(1)
        btn_lay.addWidget(self.btn)
        self.lay.addLayout(btn_lay)
        
        self.preview = GestureCameraPreview()
        self.preview.setFixedSize(280, 210)
        self.preview.hide()
        
        self.lay.addWidget(self.preview)
        
    def toggle_preview(self):
        if self.preview.isVisible():
            self.preview.hide()
            self.preview._stop_camera()
            self.btn.setStyleSheet(
                "QPushButton { background: #0c0e12; color: #ffffff; border: 1px solid rgba(0, 229, 255, 0.20); border-radius: 8px; font-weight: bold; }"
                "QPushButton:hover { color: #00e5ff; border: 1px solid #00e5ff; }"
            )
        else:
            self.preview.show()
            self.preview._start_camera()
            self.btn.setStyleSheet(
                "QPushButton { background: rgba(0, 229, 255, 0.15); color: #00e5ff; border: 1px solid #00e5ff; border-radius: 8px; font-weight: bold; }"
                "QPushButton:hover { background: rgba(0, 229, 255, 0.25); }"
            )
        self.adjustSize()
        if self.window() and hasattr(self.window(), 'resizeEvent'):
            self.window().resizeEvent(None)


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



