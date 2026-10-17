"""The animated background widget, moved verbatim out of ui.py (P4-19).
Qt/stdlib imports are direct; the handle-based QtWebEngine import is
replicated verbatim as a conditional try/except; BASE_DIR is injected via
bind() after ui.py defines it."""
from __future__ import annotations

import json
import os
import time

from PyQt6.QtCore import (QTimer, QUrl, Qt, pyqtSignal)
from PyQt6.QtGui import (QColor, QPainter, QPixmap)
from PyQt6.QtWidgets import (QWidget)
from core.ui_theme import (C)
from pathlib import (Path)

import importlib.util as _importlib_util

# QtWebEngineWidgets is imported lazily in _new_web_engine_view() below. An
# eager module-level import starts Chromium's global threads; when the Qt test
# process later finalizes, those threads race the Qt teardown and corrupt the
# heap (P3-20: exit 0xC0000374 *after* a green suite). Probing the spec does not
# execute the module, so `import core.ui_bg` (and `import ui`, which pulls it in)
# never starts Chromium.
WEB_ENGINE_AVAILABLE = (
    _importlib_util.find_spec("PyQt6.QtWebEngineWidgets") is not None
)


def _new_web_engine_view(parent):
    """Import QtWebEngineWidgets on first real use and build the view.

    Returns None (and latches WEB_ENGINE_AVAILABLE off) if the module cannot be
    imported, matching the old module-level try/except behaviour without paying
    the Chromium-startup cost at import time.
    """
    global WEB_ENGINE_AVAILABLE
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except Exception:
        WEB_ENGINE_AVAILABLE = False
        return None
    return QWebEngineView(parent)


def bind(*, BASE_DIR):
    globals().update(BASE_DIR=BASE_DIR)

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
            view = _new_web_engine_view(self)
            if view is None:
                return
            self._web_view = view
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
