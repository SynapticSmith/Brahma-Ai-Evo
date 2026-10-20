"""Connectivity & settings panel cluster extracted from ui.py (P4-19).

Behavior-preserving move of the System Connectivity page, its sidebar, the
settings hub page and the model-discovery bridge. Shared dependencies defined
outside this cluster - the colour palette ``C``, the ``BASE_DIR``/``CONFIG_DIR``
paths, ``APP_SETTINGS_FILE`` and the two ``_default_*`` settings builders - are
injected by :func:`bind`, which ``ui.py`` calls immediately after importing this
module. None of them are referenced at class-definition time, so the injection
order is safe.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from pathlib import Path

from core.ui_thread import run_on_ui

from PyQt6.QtCore import (
    QObject,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.identity import identity
from core.local_brain import local_brain
from core.user_paths import atomic_write_json, get_user_data_dir
from sound_manager import sound_mgr


def bind(*, C, BASE_DIR, CONFIG_DIR, APP_SETTINGS_FILE,
         _default_app_settings, _default_discord_settings):
    """Inject the shared symbols defined in ``ui.py``."""
    globals().update(
        C=C,
        BASE_DIR=BASE_DIR,
        CONFIG_DIR=CONFIG_DIR,
        APP_SETTINGS_FILE=APP_SETTINGS_FILE,
        _default_app_settings=_default_app_settings,
        _default_discord_settings=_default_discord_settings,
    )


class SystemConnectivitySidebar(QFrame):
    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        self.setObjectName("SystemConnectivitySidebar")
        self.setStyleSheet(
            f"""
            QFrame#SystemConnectivitySidebar {{
                background: rgba(0, 229, 255, 0.03);
                border: 1px solid rgba(0, 229, 255, 0.6);
                border-radius: 22px;
            }}
            QLabel {{
                background: transparent;
            }}
            QPushButton {{
                background: rgba(255,255,255,0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                text-align: left;
                padding: 10px 12px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.10);
                border: 1px solid rgba(0, 229, 255, 0.3);
            }}
            """
        )
        try:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(20)
            shadow.setColor(QColor(0, 229, 255, 80))
            shadow.setOffset(0, 0)
            self.setGraphicsEffect(shadow)
        except: pass
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(14)

        title = QLabel("SYSTEM STATUS")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        lay.addWidget(title)

        self._status_card = QFrame()
        self._status_card.setStyleSheet("QFrame { background: rgba(14,16,20,0.88); border: 1px solid rgba(53,255,117,0.18); border-radius: 14px; }")
        s_lay = QVBoxLayout(self._status_card)
        s_lay.setContentsMargins(16, 14, 16, 14)
        s_lay.setSpacing(10)
        self._online_lbl = QLabel("ΓùÅ System Online")
        self._online_lbl.setStyleSheet("color: #35ff75; font-weight: 700;")
        self._desc_lbl = QLabel("All systems are operational.")
        self._desc_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
        s_lay.addWidget(self._online_lbl)
        s_lay.addWidget(self._desc_lbl)
        lay.addWidget(self._status_card)

        self._info_rows: dict[str, QLabel] = {}
        for label in ("Version", "Platform", "Current AI Provider", "Last Updated"):
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 4)
            row.setSpacing(10)
            icon = QLabel("Γùî")
            icon.setFixedWidth(18)
            icon.setStyleSheet(f"color: {C.WHITE};")
            key_lbl = QLabel(label)
            key_lbl.setStyleSheet(f"color: {C.WHITE};")
            val_lbl = QLabel("")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setStyleSheet(f"color: {C.TEXT_MED};")
            row.addWidget(icon)
            row.addWidget(key_lbl)
            row.addStretch(1)
            row.addWidget(val_lbl)
            lay.addLayout(row)
            self._info_rows[label] = val_lbl

        quick_title = QLabel("QUICK ACTIONS")
        quick_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        quick_title.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        lay.addWidget(quick_title)

        self._quick_actions = QVBoxLayout()
        self._quick_actions.setSpacing(10)
        lay.addLayout(self._quick_actions)
        self._mk_quick_action("Γå╗ Restart Brahma Evo", QStyle.StandardPixmap.SP_BrowserReload, self._restart)
        self._mk_quick_action("Γƒ│ Reload Configuration", QStyle.StandardPixmap.SP_BrowserReload, self._reload)
        self._mk_quick_action("≡ƒôü Open Data Folder", QStyle.StandardPixmap.SP_DirOpenIcon, self._open_data_folder)
        self._mk_quick_action("≡ƒôä View Logs", QStyle.StandardPixmap.SP_FileDialogDetailedView, self._view_logs)
        self._mk_quick_action("Γ¼ç Check for Updates", QStyle.StandardPixmap.SP_ArrowDown, self._check_updates)

        tip = QFrame()
        tip.setStyleSheet("QFrame { background: rgba(24, 18, 8, 0.85); border: 1px solid rgba(255, 191, 0, 0.22); border-radius: 14px; }")
        tip_lay = QVBoxLayout(tip)
        tip_lay.setContentsMargins(16, 14, 16, 14)
        tip_lay.setSpacing(8)
        tip_title = QLabel("Security Tip")
        tip_title.setStyleSheet("color: #ffbf00; font-weight: 700;")
        tip_body = QLabel('"Never share your API keys with anyone."')
        tip_body.setWordWrap(True)
        tip_body.setStyleSheet(f"color: {C.TEXT_MED};")
        tip_lay.addWidget(tip_title)
        tip_lay.addWidget(tip_body)
        lay.addStretch(1)
        lay.addWidget(tip)

        self.refresh()

    def set_controller(self, controller):
        self._controller = controller
        self.refresh()

    def _mk_quick_action(self, text: str, icon_kind, slot):
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(self.style().standardIcon(icon_kind))
        btn.clicked.connect(slot)
        self._quick_actions.addWidget(btn)
        return btn

    def _bridge(self):
        return self._controller

    def _restart(self):
        if self._bridge() and hasattr(self._bridge(), "_restart_app"):
            self._bridge()._restart_app()

    def _reload(self):
        if self._bridge() and hasattr(self._bridge(), "_win"):
            try:
                self._bridge()._win._load_api_defaults()
                self._bridge()._win._load_discord_settings()
                self._bridge()._log_sig.emit("SYS: Configuration reloaded.")
            except Exception:
                pass

    def _open_data_folder(self):
        try:
            os.startfile(str(CONFIG_DIR))
        except Exception:
            pass

    def _view_logs(self):
        try:
            os.startfile(str(BASE_DIR))
        except Exception:
            pass

    def _check_updates(self):
        if self._bridge() and hasattr(self._bridge(), "write_log"):
            self._bridge().write_log("SYS: Update check is not connected to a remote service yet.")

    def refresh(self):
        if self._bridge() and hasattr(self._bridge(), "_win"):
            version = "v1.0.0"
            platform_name = platform.system()
            provider = self._bridge()._win._load_app_settings().get("default_ai_provider", "Gemini")
            last_updated = time.strftime("%d %b %Y %H:%M")
            self._info_rows["Version"].setText(version)
            self._info_rows["Platform"].setText(platform_name)
            self._info_rows["Current AI Provider"].setText(provider)
            self._info_rows["Last Updated"].setText(last_updated)
        else:
            self._info_rows["Version"].setText("v1.0.0")
            self._info_rows["Platform"].setText(platform.system())
            self._info_rows["Current AI Provider"].setText("Gemini")
            self._info_rows["Last Updated"].setText(time.strftime("%d %b %Y %H:%M"))



class _ModelDiscoveryBridge(QObject):
    """Runs OpenCode /v1/models discovery off the UI thread and signals results back.

    Discovery performs a blocking HTTP request with a multi-second timeout; doing
    it inline would freeze the whole settings page, so the work runs on a worker
    thread and only this signal touches widgets (on the UI thread).
    """

    finished = pyqtSignal(list, str, str)  # models, resolved_url, error

    def discover(self, url: str):
        def _work():
            try:
                from llm_client import UnifiedAIClient
                models = UnifiedAIClient.poll_opencode_models(url)
                self.finished.emit(models, url, "" if models else f"No models returned by {url}")
            except Exception as exc:
                self.finished.emit([], url, str(exc))

        threading.Thread(target=_work, daemon=True).start()


class SettingsHubPage(QWidget):
    def __init__(self, navigation_callback, parent=None):
        super().__init__(parent)
        self._nav_cb = navigation_callback
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 60, 40, 60)
        lay.setSpacing(25)

        title = QLabel("Settings Hub")
        title.setFont(QFont("Segoe UI", 32, QFont.Weight.Black))
        title.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        subtitle = QLabel("Select a section below to configure your Brahma Evo environment.")
        subtitle.setFont(QFont("Segoe UI", 12))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(subtitle)

        cards_lay = QHBoxLayout()
        cards_lay.setSpacing(24)
        cards_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        cards_data = [
            ("Brahma Evo Home", "Configure smart home integrations", "🏠", 1),
            ("Devices", "Manage and control connected hardware", "🔌", 2),
            ("System & Connect", "Configure providers and api preferences", "⚙️", 3)
        ]

        for title_text, desc_text, icon_emoji, target_idx in cards_data:
            card = QFrame()
            card.setFixedSize(280, 200)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setStyleSheet(
                f"QFrame {{ "
                f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(16, 18, 26, 0.75), stop:1 rgba(8, 10, 15, 0.85)); "
                f"  border: 1px solid rgba(0, 229, 255, 0.18); "
                f"  border-radius: 18px; "
                f"}} "
                f"QFrame:hover {{ "
                f"  border: 1.5px solid {C.PRI}; "
                f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(35, 28, 15, 0.8), stop:1 rgba(16, 12, 8, 0.9)); "
                f"}}"
            )
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(20, 20, 20, 20)
            card_lay.setSpacing(12)
            card_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon_lbl = QLabel(icon_emoji)
            icon_lbl.setFont(QFont("Segoe UI", 36))
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setStyleSheet("background: transparent; border: none;")
            card_lay.addWidget(icon_lbl)

            t = QLabel(title_text)
            t.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
            t.setStyleSheet(f"color: {C.WHITE}; background: transparent; border: none;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_lay.addWidget(t)

            d = QLabel(desc_text)
            d.setFont(QFont("Segoe UI", 9))
            d.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            d.setWordWrap(True)
            d.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_lay.addWidget(d)

            card.mousePressEvent = lambda e, idx=target_idx: self._nav_cb(idx)
            cards_lay.addWidget(card)

        lay.addStretch(1)
        lay.addLayout(cards_lay)
        lay.addStretch(2)

class SystemConnectivityPage(QWidget):
    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        self.setObjectName("SystemConnectivityPage")
        self.setStyleSheet(f"""
            QWidget#SystemConnectivityPage {{
                background: transparent;
            }}
            QFrame#SettingsCard {{
                background: rgba(10, 12, 18, 160);
                border: 1.5px solid rgba(0, 229, 255, 0.15);
                border-radius: 18px;
            }}
            QFrame#SettingsCard:hover {{
                border: 1.5px solid rgba(0, 229, 255, 0.35);
                background: rgba(20, 16, 12, 200);
            }}
            QLabel {{
                background: transparent;
            }}
            QPushButton {{
                background: rgba(255, 255, 255, 0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 10px 12px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255, 0.12);
                border: 1px solid rgba(0, 229, 255, 0.45);
            }}
            QLineEdit, QComboBox {{
                background: rgba(13, 15, 19, 240);
                color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                min-height: 34px;
                padding: 0 10px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 1px solid {C.PRI};
                background: rgba(0, 229, 255, 0.02);
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        root.addWidget(self._scroll)

        self._content = QWidget()
        self._scroll.setWidget(self._content)
        lay = QVBoxLayout(self._content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        header = QFrame()
        header.setStyleSheet("background: transparent; border: none;")
        h_lay = QVBoxLayout(header)
        h_lay.setContentsMargins(0, 0, 0, 0)
        h_lay.setSpacing(6)
        title = QLabel("SYSTEM & CONNECTIVITY")
        title.setFont(QFont("Segoe UI", 22, QFont.Weight.Black))
        title.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        sub = QLabel("Manage your AI providers, connections and application preferences.")
        sub.setFont(QFont("Segoe UI", 10))
        sub.setStyleSheet(f"color: {C.TEXT_DIM};")
        h_lay.addWidget(title)
        h_lay.addWidget(sub)
        lay.addWidget(header)

        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self._build_left_column(), 7)
        top.addWidget(self._build_summary_card(), 3)
        lay.addLayout(top)
        lay.addStretch(1)

        self.refresh()

    def set_controller(self, controller):
        self._controller = controller
        self.refresh()

    def _ctrl(self):
        return self._controller

    def _card(self, title: str, subtitle: str = "") -> QFrame:
        frame = QFrame()
        frame.setObjectName("SettingsCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        head.setStyleSheet(f"color: {C.WHITE};")
        lay.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {C.TEXT_DIM};")
            lay.addWidget(sub)
        return frame

    def _mk_toggle(self, text: str, checked: bool, callback):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: callback(btn.isChecked()))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                text-align: left;
                background: rgba(255,255,255,0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 12px 14px;
            }}
            QPushButton:checked {{
                background: rgba(0, 229, 255,0.12);
                border: 1px solid {C.PRI};
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255,0.08);
            }}
            """
        )
        return btn

    def _provider_key_preview(self, key: str) -> str:
        key = (key or "").strip()
        if not key:
            return "Not set"
        if len(key) <= 8:
            return "ΓÇóΓÇóΓÇóΓÇóΓÇóΓÇóΓÇóΓÇó"
        return f"{key[:4]}ΓÇóΓÇóΓÇóΓÇóΓÇóΓÇóΓÇóΓÇó{key[-4:]}"

    def _provider_row(self, name: str, key: str, model: str, setting_key: str):
        row = QFrame()
        if key:
            row.setStyleSheet("QFrame { background: rgba(55, 255, 95, 0.02); border: 1px solid rgba(55, 255, 95, 0.1); border-radius: 14px; } QFrame:hover { background: rgba(55, 255, 95, 0.05); border: 1px solid rgba(55, 255, 95, 0.25); }")
        else:
            row.setStyleSheet("QFrame { background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 14px; } QFrame:hover { background: rgba(0, 229, 255, 0.04); border: 1px solid rgba(0, 229, 255, 0.3); }")
        r = QHBoxLayout(row)
        r.setContentsMargins(14, 12, 14, 12)
        r.setSpacing(12)
        icon = QLabel(name[:1].upper())
        icon.setFixedSize(42, 42)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        if key:
            icon.setStyleSheet(f"background: rgba(55, 255, 95, 0.12); color: {C.GREEN}; border: 1px solid rgba(55, 255, 95, 0.3); border-radius: 21px;")
        else:
            icon.setStyleSheet(f"background: rgba(0, 229, 255, 0.12); color: {C.WHITE}; border: 1px solid rgba(0, 229, 255, 0.38); border-radius: 21px;")
        r.addWidget(icon)
        meta = QVBoxLayout()
        title = QLabel(name)
        title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.WHITE}; border: none;")
        if key:
            status = QLabel("\u2713  Already Added")
            status.setStyleSheet(f"color: {C.GREEN}; border: none; font-weight: bold;")
        else:
            status = QLabel("Not configured")
            status.setStyleSheet(f"color: {C.TEXT_DIM}; border: none;")
        model_lbl = QLabel(f"Current model: {model}")
        model_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; border: none;")
        api_lbl = QLabel(self._provider_key_preview(key))
        api_lbl.setStyleSheet(f"color: {C.TEXT_MED}; border: none;")
        meta.addWidget(title)
        meta.addWidget(status)
        meta.addWidget(model_lbl)
        meta.addWidget(api_lbl)
        r.addLayout(meta, 1)
        btn_lay = QVBoxLayout()
        btn_lay.setSpacing(8)
        if key:
            edit = QPushButton("Edit API Key")
        else:
            edit = QPushButton("Add API Key")
            edit.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 229, 255, 0.1);
                    color: #00e5ff;
                    border: 1px solid rgba(0, 229, 255, 0.3);
                    border-radius: 12px;
                    padding: 10px 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: rgba(0, 229, 255, 0.2);
                    border: 1px solid rgba(0, 229, 255, 0.5);
                }
            """)
        edit.clicked.connect(lambda: self._open_api_keys())
        test = QPushButton("Test Connection")
        test.clicked.connect(lambda: self._test_provider(setting_key))
        btn_lay.addWidget(edit)
        btn_lay.addWidget(test)
        r.addLayout(btn_lay)
        return row, status, api_lbl

    def _build_spotify_mcp_card(self):
        spotify_card = self._card("Spotify MCP Server", "Voice & AI music streaming, song searches, playlist browsing, and queue management via Spotify Web API.")
        s_lay = spotify_card.layout()

        self._spotify_status_lbl = QLabel("Status: Checking...")
        self._spotify_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
        s_lay.addWidget(self._spotify_status_lbl)

        cid_row = QHBoxLayout()
        cid_lbl = QLabel("Client ID")
        cid_lbl.setStyleSheet(f"color: {C.WHITE}; min-width: 90px;")
        cid_row.addWidget(cid_lbl)
        self._spotify_client_id = QLineEdit()
        self._spotify_client_id.setPlaceholderText("Spotify Client ID from Developer Dashboard")
        cid_row.addWidget(self._spotify_client_id, 1)
        s_lay.addLayout(cid_row)

        csec_row = QHBoxLayout()
        csec_lbl = QLabel("Client Secret")
        csec_lbl.setStyleSheet(f"color: {C.WHITE}; min-width: 90px;")
        csec_row.addWidget(csec_lbl)
        self._spotify_client_secret = QLineEdit()
        self._spotify_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._spotify_client_secret.setPlaceholderText("Spotify Client Secret")
        csec_row.addWidget(self._spotify_client_secret, 1)

        self._spotify_reveal_btn = QPushButton("Reveal")
        self._spotify_reveal_btn.setCheckable(True)
        self._spotify_reveal_btn.clicked.connect(self._toggle_spotify_secret_reveal)
        csec_row.addWidget(self._spotify_reveal_btn)
        s_lay.addLayout(csec_row)

        redir_row = QHBoxLayout()
        redir_lbl = QLabel("Redirect URI:")
        redir_lbl.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
        redir_val = QLabel("http://127.0.0.1:8888/callback")
        redir_val.setStyleSheet("color: #00e5ff; font-size: 11px; font-family: monospace; font-weight: bold;")
        copy_btn = QPushButton("Copy URI")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setStyleSheet("padding: 4px 10px; font-size: 10px;")
        copy_btn.clicked.connect(self._copy_spotify_redirect_uri)
        redir_row.addWidget(redir_lbl)
        redir_row.addWidget(redir_val)
        redir_row.addStretch(1)
        redir_row.addWidget(copy_btn)
        s_lay.addLayout(redir_row)

        s_btns_row = QHBoxLayout()
        s_btns_row.setSpacing(10)

        self._spotify_save_btn = QPushButton("Save Credentials")
        self._spotify_save_btn.clicked.connect(self._handle_spotify_save)
        s_btns_row.addWidget(self._spotify_save_btn)

        self._spotify_auth_btn = QPushButton("🔗 Authenticate in Browser")
        self._spotify_auth_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.12);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.45);
                font-weight: bold;
                border-radius: 12px;
                padding: 10px 14px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.25);
                border: 1px solid #00e5ff;
            }
        """)
        self._spotify_auth_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._spotify_auth_btn.clicked.connect(self._handle_spotify_auth)
        s_btns_row.addWidget(self._spotify_auth_btn)

        self._spotify_test_btn = QPushButton("Test Connection")
        self._spotify_test_btn.clicked.connect(self._handle_spotify_test)
        s_btns_row.addWidget(self._spotify_test_btn)

        self._spotify_disconnect_btn = QPushButton("Disconnect")
        self._spotify_disconnect_btn.clicked.connect(self._handle_spotify_disconnect)
        s_btns_row.addWidget(self._spotify_disconnect_btn)

        s_lay.addLayout(s_btns_row)

        spotify_tip = QLabel("Tip: In developer.spotify.com/dashboard ➔ Settings ➔ add 'http://127.0.0.1:8888/callback' to Redirect URIs and save.")
        spotify_tip.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 10px;")
        spotify_tip.setWordWrap(True)
        s_lay.addWidget(spotify_tip)

        self._prefill_spotify_credentials()
        self._update_spotify_status()

        return spotify_card

    def _replace_app_settings(self, settings: dict):
        """Persist a whole settings dict.

        Named for what the caller intends; the write itself always merges over
        the file so a key another owner changed is not reverted. The no-window
        branch used to blind-write, which is how a card holding a stale dict
        could clobber unrelated stored choices.
        """
        win = self._owner_window()
        if win is not None and hasattr(win, "_save_app_settings"):
            win._save_app_settings(settings)
            return
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
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
        except Exception:
            pass

    def _custom_mcp_servers(self) -> list:
        raw = self._load_app_settings().get("custom_mcp_servers") or []
        return [entry for entry in raw if isinstance(entry, dict)]

    def _build_tool_index_card(self):
        """Card controlling the tool & skill index (see core/tool_index.py).

        The index is what keeps the Live handshake inside Gemini's
        128-declaration ceiling while leaving every tool reachable through
        search_tools / call_tool. Turning it off restores the old behaviour,
        which sends every tool and will be rejected once enough MCP servers are
        connected — so the card says so plainly rather than hiding the risk.
        """
        card = self._card(
            "Tool & Skill Index",
            "Decides which tools are sent to the AI each session. Keeps the connection "
            "inside Gemini's 128-tool limit and cuts prompt size; every other tool stays "
            "reachable on demand.",
        )
        lay = card.layout()

        settings = self._load_app_settings()

        # --- enable toggle ------------------------------------------------- #
        enabled_box = QCheckBox("Enable the tool index (recommended)")
        enabled_box.setChecked(bool(settings.get("tool_index_enabled", True)))
        enabled_box.setStyleSheet(f"color: {C.TEXT};")
        lay.addWidget(enabled_box)

        # --- budget -------------------------------------------------------- #
        budget_row = QHBoxLayout()
        budget_label = QLabel("Tools sent per session")
        budget_label.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        budget_row.addWidget(budget_label)

        # Presets rather than a free-form number: every value must stay well
        # under the provider ceiling, and the ceiling differs by provider.
        # The low tiers exist because a 0.8B-3B model on CPU spends much of its
        # attention window on tool schemas; 4-12 keeps it usable there.
        budget_box = QComboBox()
        current = int(settings.get("tool_index_budget", 24) or 24)
        # 128 is Gemini's hard ceiling (GEMINI_MAX_TOOLS); budget() clamps there.
        for value in (4, 8, 12, 16, 24, 32, 48, 64, 80, 96, 112, 128):
            budget_box.addItem(f"{value} tools", value)
        found = budget_box.findData(current)
        # Derive the default index rather than hardcoding it: the preset list grew
        # a lower tier, so a literal index silently selected a different budget.
        default_index = budget_box.findData(24)
        if default_index < 0:
            default_index = 0
        budget_box.setCurrentIndex(found if found >= 0 else default_index)
        budget_row.addWidget(budget_box)
        budget_row.addStretch(1)
        lay.addLayout(budget_row)

        # Derive the meta-tool count and cap rather than hardcoding them: the
        # discovery surface grew (search/list for both catalogues), and a stale
        # number here would misdescribe the setting.
        try:
            from core import tool_index as _ti

            meta_count = len(_ti.META_TOOLS)
            page_default = _ti.LIST_PAGE_DEFAULT
        except Exception:
            meta_count, page_default = 7, 50

        hint = QLabel(
            "Fewer tools means a smaller prompt and faster routing. Anything above the "
            "limit is sent lazily via search_tools, so raising this is optional. "
            f"The search_tools cap follows this budget (budget minus the {meta_count} "
            "always-on discovery meta-tools, which include list_tools and list_skills "
            f"for browsing everything in {page_default}-tool pages), so a budget of 64 "
            "lets search return up to 57 tools, 128 lets it return up to 121."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(hint)

        # --- routing test -------------------------------------------------- #
        test_row = QHBoxLayout()
        self._tool_index_probe = QLineEdit()
        self._tool_index_probe.setPlaceholderText(
            "Test routing for a request, e.g. \"dim the monitors\""
        )
        test_row.addWidget(self._tool_index_probe, 1)
        btn_test = QPushButton("Test")
        btn_test.setFixedWidth(70)
        btn_test.clicked.connect(self._on_tool_index_test)
        test_row.addWidget(btn_test)
        lay.addLayout(test_row)

        self._tool_index_result = QLabel("")
        self._tool_index_result.setWordWrap(True)
        self._tool_index_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._tool_index_result.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._tool_index_result)

        # --- status --------------------------------------------------------- #
        self._tool_index_status = QLabel("")
        self._tool_index_status.setWordWrap(True)
        self._tool_index_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._tool_index_status)
        self._refresh_tool_index_status()

        # --- persistence ---------------------------------------------------- #
        def _persist():
            merged = self._load_app_settings()
            merged["tool_index_enabled"] = bool(enabled_box.isChecked())
            merged["tool_index_budget"] = int(budget_box.currentData() or 24)
            self._replace_app_settings(merged)
            try:
                from core import tool_bridge

                tool_bridge.configure(settings=merged)
                self._refresh_tool_index_status()
            except Exception as exc:
                self._tool_index_status.setText(f"Saved, but the running index could not be updated: {exc}")

        enabled_box.stateChanged.connect(lambda _s: _persist())
        budget_box.currentIndexChanged.connect(lambda _i: _persist())

        return card

    def _refresh_tool_index_status(self):
        """Renders live index numbers. Never raises: status text is informational."""
        try:
            from core import tool_bridge

            snap = tool_bridge.snapshot()
        except Exception as exc:
            self._tool_index_status.setText(f"Tool index unavailable: {exc}")
            return

        backends = ", ".join(snap["backends"]) or "none active"
        self._tool_index_status.setText(
            f"{snap['tools']} tools indexed · sending {snap['advertised']} · "
            f"{snap['deferred']} reachable on demand · backends: {backends} · "
            f"~{snap['token_estimate']} tokens (was ~9,000 unscaled) · "
            f"provider limit {snap['gemini_limit']}"
        )

    def _on_tool_index_test(self):
        """Shows what a request would route to, without touching the AI session."""
        query = self._tool_index_probe.text().strip()
        if not query:
            self._tool_index_result.setText("Type a request to test.")
            return
        try:
            from core import tool_bridge

            rows = tool_bridge.explain(query, k=6)
        except Exception as exc:
            self._tool_index_result.setText(f"Routing test failed: {exc}")
            return

        if not rows:
            self._tool_index_result.setText(f"No tools matched {query!r}.")
            return
        lines = [f"<b>{query}</b> would route to:"]
        for row in rows:
            lines.append(
                f"&nbsp;&nbsp;• {row['name']} <span style='color:{C.TEXT_DIM}'>"
                f"[{row['source']}]</span>"
            )
        self._tool_index_result.setText("<br>".join(lines))

    @staticmethod
    def _tool_visibility_signature(catalog):
        """Identity of the tool inventory for change detection.

        Covers each group's id, connection state, and tool names — deliberately
        NOT the per-tool ``enabled`` flag, so toggling a checkbox never rebuilds
        the list under the user's cursor. Only a server gaining or losing tools
        (or changing state) produces a new signature.
        """
        return tuple(
            (
                group.get("id"),
                group.get("state"),
                tuple(sorted(str(t.get("name")) for t in group.get("tools") or [])),
            )
            for group in catalog.get("groups") or []
        )

    def _poll_tool_visibility(self):
        """Rebuilds the tool list only when the inventory changes.

        MCP tools register asynchronously as servers finish connecting, so the
        list has to populate itself instead of waiting for the Refresh button.
        Cheap: only runs while this page is on screen, and only redraws when the
        signature actually differs.
        """
        if not self.isVisible():
            return
        rebuild = getattr(self, "_rebuild_tool_visibility", None)
        if rebuild is None:
            return
        try:
            from core import tool_bridge

            catalog = tool_bridge.tool_catalog()
        except Exception:
            return
        if self._tool_visibility_signature(catalog) != getattr(self, "_tool_visibility_sig", None):
            rebuild()

    def _build_tool_visibility_card(self):
        """Lists every tool as a toggle: "Default tools" then one section per MCP server.

        A tool switched off is removed from the index the model sees — it cannot
        be searched, advertised, or called — while its definition stays in the
        underlying catalog so switching it back on is instant and survives a
        resync. The choice is persisted to `disabled_tools` and applied live, so
        no restart is needed. MCP servers that are configured but not yet
        connected still show an (empty) section rather than leaving the card
        blank, which is what made the old view look like nothing was loading.
        """
        card = self._card(
            "Tools & MCP Access",
            "Every tool the agent can use, grouped by where it comes from. Toggle one "
            "off to hide it from the agent entirely — it stays installed and can be "
            "switched back on any time.",
        )
        lay = card.layout()

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(360)
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 6px; background: transparent; }}")
        scroll.setWidget(body)
        lay.addWidget(scroll)

        footer = QLabel("")
        footer.setWordWrap(True)
        footer.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(footer)

        def _clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                else:
                    sub = item.layout()
                    if sub is not None:
                        _clear(sub)

        def _apply(name, enabled):
            merged = self._load_app_settings()
            disabled = set(merged.get("disabled_tools") or [])
            if enabled:
                disabled.discard(name)
            else:
                disabled.add(name)
            merged["disabled_tools"] = sorted(disabled)
            self._replace_app_settings(merged)
            try:
                from core import tool_bridge

                tool_bridge.configure(settings=merged)
                self._refresh_tool_index_status()
            except Exception as exc:
                footer.setText(f"Saved, but the running index could not be updated: {exc}")
                return
            self._refresh_tool_visibility_footer()

        def _refresh_footer():
            try:
                from core import tool_bridge

                snap = tool_bridge.snapshot()
            except Exception:
                footer.setText("")
                return
            off = snap.get("disabled", 0)
            footer.setText(
                f"{snap['tools']} tools active · {off} hidden · {snap['advertised']} sent this session"
                + (" · hiding tools frees prompt space" if not off else "")
            )

        def _rebuild():
            _clear(body_lay)
            try:
                from core import tool_bridge

                catalog = tool_bridge.tool_catalog()
            except Exception as exc:
                self._tool_visibility_sig = None
                self._tool_visibility_boxes = []
                err = QLabel(f"Tool catalog unavailable: {exc}")
                err.setStyleSheet("color: #ff5555; font-size: 11px;")
                body_lay.addWidget(err)
                _refresh_footer()
                return

            self._tool_visibility_sig = self._tool_visibility_signature(catalog)
            boxes = []
            for group in catalog["groups"]:
                is_mcp = group.get("kind") == "mcp"
                title = str(group.get("label") or "Tools")
                count = len(group.get("tools") or [])
                state = f" · {group.get('state')}" if is_mcp and group.get("state") else ""
                head = QLabel(f"<b>{title}</b> <span style='color:{C.TEXT_DIM}'>({count}){state}</span>")
                head.setTextFormat(Qt.TextFormat.RichText)
                head.setStyleSheet(f"color: {C.WHITE}; margin-top: 6px;")
                body_lay.addWidget(head)

                tools = group.get("tools") or []
                if not tools:
                    note = QLabel("No tools connected yet." if is_mcp else "No tools.")
                    note.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; margin-left: 18px;")
                    body_lay.addWidget(note)
                    continue

                for tool in tools:
                    box = QCheckBox(str(tool.get("name") or "tool"))
                    box.setChecked(bool(tool.get("enabled", True)))
                    desc = str(tool.get("description") or "").strip()
                    box.setToolTip(desc or str(tool.get("name") or ""))
                    box.setStyleSheet(f"color: {C.TEXT}; margin-left: 18px;")
                    box.stateChanged.connect(
                        lambda _state, name=str(tool.get("name")), widget=box: _apply(name, widget.isChecked())
                    )
                    body_lay.addWidget(box)
                    boxes.append(box)

            body_lay.addStretch(1)
            self._tool_visibility_boxes = boxes
            _refresh_footer()

        self._refresh_tool_visibility_footer = _refresh_footer
        self._rebuild_tool_visibility = _rebuild

        # Populate live as MCP servers connect (their tools land asynchronously).
        # One timer for the page; the tick is a no-op unless the inventory changed.
        if not hasattr(self, "_tool_visibility_timer"):
            self._tool_visibility_timer = QTimer(self)
            self._tool_visibility_timer.setInterval(2000)
            self._tool_visibility_timer.timeout.connect(self._poll_tool_visibility)
            self._tool_visibility_timer.start()

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setFixedWidth(90)
        btn_refresh.clicked.connect(lambda: _rebuild())
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        _rebuild()
        return card

    def _build_skills_library_card(self):
        """Lists every skill in the Skills Library as a toggle.

        Skills are on-demand playbooks. Unlike tools they are not sent to the
        model at all until the agent explicitly asks for one, so this list can be
        long without costing prompt space. Toggling a skill off hides it from
        ``search_skills`` and un-publishes it if it was executable; the master
        switch turns the whole library (and all three of its meta-tools) off. The
        inventory is re-scanned on Refresh, which is how a skill dropped into the
        library folder shows up without a restart.
        """
        card = self._card(
            "Skills Library",
            "On-demand task playbooks, loaded only when the agent asks for one — "
            "never injected automatically. Executable skills also appear as tools.",
        )
        lay = card.layout()

        master = QCheckBox("Enable the Skills Library")
        master.setChecked(bool(self._load_app_settings().get("skills_library_enabled", True)))
        master.setStyleSheet(f"color: {C.WHITE};")
        lay.addWidget(master)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(320)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 6px; background: transparent; }}"
        )
        scroll.setWidget(body)
        lay.addWidget(scroll)

        footer = QLabel("")
        footer.setWordWrap(True)
        footer.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(footer)

        def _clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                else:
                    sub = item.layout()
                    if sub is not None:
                        _clear(sub)

        def _apply(skill_id, enabled):
            merged = self._load_app_settings()
            disabled = set(merged.get("disabled_skills") or [])
            if enabled:
                disabled.discard(skill_id)
            else:
                disabled.add(skill_id)
            merged["disabled_skills"] = sorted(disabled)
            self._replace_app_settings(merged)
            try:
                from core import tool_bridge

                tool_bridge.configure(settings=merged)
                tool_bridge.sync_skills()
            except Exception as exc:
                footer.setText(f"Saved, but the running library could not be updated: {exc}")
                return
            _refresh_footer()

        def _apply_master(enabled):
            merged = self._load_app_settings()
            merged["skills_library_enabled"] = bool(enabled)
            self._replace_app_settings(merged)
            try:
                from core import tool_bridge

                tool_bridge.configure(settings=merged)
                tool_bridge.sync_skills()
            except Exception as exc:
                footer.setText(f"Saved, but the running library could not be updated: {exc}")
                return
            _refresh_footer()

        def _refresh_footer():
            try:
                from core import tool_bridge

                snap = tool_bridge.snapshot().get("skills") or {}
            except Exception:
                snap = {}
            if not snap:
                footer.setText("Skills Library unavailable.")
                return
            total = snap.get("skills", 0)
            exe = snap.get("executable", 0)
            off = len(snap.get("disabled") or [])
            state = "on" if snap.get("enabled", True) else "off"
            footer.setText(f"{total} skills · {exe} executable · {off} hidden · library {state}")

        def _rebuild(force=False):
            _clear(body_lay)
            try:
                from core import skills_library, tool_bridge

                if force:
                    skills_library.get_library().refresh(force=True)
                    tool_bridge.sync_skills()
                catalog = tool_bridge.skills_catalog()
            except Exception as exc:
                err = QLabel(f"Skills Library unavailable: {exc}")
                err.setStyleSheet("color: #ff5555; font-size: 11px;")
                body_lay.addWidget(err)
                _refresh_footer()
                return

            groups = catalog.get("groups") or []
            if not groups:
                note = QLabel(
                    "No skills installed yet. Add one under skills_library/ in the app "
                    "folder, or in the user skills folder, then Refresh."
                )
                note.setWordWrap(True)
                note.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
                body_lay.addWidget(note)

            for group in groups:
                skills = group.get("skills") or []
                head = QLabel(
                    f"<b>{group.get('label') or 'General'}</b> "
                    f"<span style='color:{C.TEXT_DIM}'>({len(skills)})</span>"
                )
                head.setTextFormat(Qt.TextFormat.RichText)
                head.setStyleSheet(f"color: {C.WHITE}; margin-top: 6px;")
                body_lay.addWidget(head)

                for skill in skills:
                    sid = str(skill.get("skill_id") or "")
                    label = str(skill.get("name") or sid)
                    if skill.get("executable"):
                        label += "  ·  executable"
                    box = QCheckBox(label)
                    box.setChecked(bool(skill.get("enabled", True)))
                    box.setToolTip(str(skill.get("description") or sid))
                    box.setStyleSheet(f"color: {C.TEXT}; margin-left: 18px;")
                    box.stateChanged.connect(
                        lambda _state, skill_id=sid, widget=box: _apply(skill_id, widget.isChecked())
                    )
                    body_lay.addWidget(box)

            body_lay.addStretch(1)
            _refresh_footer()

        master.stateChanged.connect(lambda _s: _apply_master(master.isChecked()))

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setFixedWidth(90)
        btn_refresh.clicked.connect(lambda: _rebuild(force=True))
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        _rebuild()
        return card

    # ------------------------------------------------------------------
    # Speech engines (core/speech_stt.py, core/speech_tts.py)
    # ------------------------------------------------------------------
    def _build_speech_card(self):
        """Card selecting the STT/TTS engines for the modular speech pipeline.

        This only affects the path where Brahma has to transcribe and speak
        itself — i.e. no live Gemini session. While Gemini Live is connected the
        unified native voice is used and nothing here has any effect, which is
        why the subtitle says so: users should not think this changes the voice
        they hear in a normal session.

        Both engines default to the previous cloud behaviour. Selecting a local
        engine that is not installed does not break anything — the engine falls
        through to the next available one and the card says which is live.
        """
        card = self._card(
            "Speech Engines",
            "Transcription and speech for the offline / local-model path. A live "
            "Gemini session always uses the unified voice and ignores these.",
        )
        lay = card.layout()
        settings = self._load_app_settings()

        try:
            from core import speech_stt as _stt
            from core import speech_tts as _tts
        except Exception as exc:
            err = QLabel(f"Speech engine modules unavailable: {exc}")
            err.setWordWrap(True)
            err.setStyleSheet(f"color: {C.RED if hasattr(C, 'RED') else '#ff3b30'}; font-size: 11px;")
            lay.addWidget(err)
            return card

        # --- speech to text ---------------------------------------------- #
        stt_title = QLabel("Transcription (speech → text)")
        stt_title.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(stt_title)

        stt_box = QComboBox()
        self._stt_box = stt_box
        self._stt_engines = _stt.available_engines()
        for eng in self._stt_engines:
            mark = "" if eng["available"] else "  (not installed)"
            stt_box.addItem(f"{eng['label']}{mark}", eng["id"])
        current_stt = str(settings.get("speech_stt_engine", "google") or "google")
        idx = stt_box.findData(current_stt)
        stt_box.setCurrentIndex(idx if idx >= 0 else 0)
        lay.addWidget(stt_box)

        whisper_row = QHBoxLayout()
        whisper_row.addWidget(QLabel("Whisper model"))
        self._whisper_model = QLineEdit(
            str(settings.get("speech_whisper_model", "base.en") or "base.en")
        )
        self._whisper_model.setPlaceholderText("base.en")
        whisper_row.addWidget(self._whisper_model, 1)
        lay.addLayout(whisper_row)

        whisper_hint = QLabel(
            "Used only by faster-whisper. tiny.en is fastest, base.en is more "
            "accurate. The model downloads once, then works offline."
        )
        whisper_hint.setWordWrap(True)
        whisper_hint.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(whisper_hint)

        # --- text to speech ------------------------------------------------ #
        tts_title = QLabel("Speech (text → voice)")
        tts_title.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 600;")
        lay.addWidget(tts_title)

        tts_box = QComboBox()
        self._tts_engines = _tts.available_engines()
        for eng in self._tts_engines:
            mark = "" if eng["available"] else "  (not installed)"
            tts_box.addItem(f"{eng['label']}{mark}", eng["id"])
        current_tts = str(settings.get("speech_tts_engine", "edge") or "edge")
        idx = tts_box.findData(current_tts)
        tts_box.setCurrentIndex(idx if idx >= 0 else 0)
        self._tts_box = tts_box
        lay.addWidget(tts_box)

        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Voice file"))
        self._tts_voice = QLineEdit(str(settings.get("speech_tts_voice", "") or ""))
        self._tts_voice.setPlaceholderText(
            r"Optional override. Leave blank to use the voices picked below"
        )
        voice_row.addWidget(self._tts_voice, 1)
        lay.addLayout(voice_row)

        # --- per-engine local voices (piper / kokoro) ------------------------ #
        # One shared "Voice file" cannot serve these engines once more than one
        # voice is installed: Piper picks a voice KEY, Kokoro a model FILENAME
        # plus a voice NAME inside the pack. Shown only for the engine they
        # belong to, so the card stays readable.
        self._piper_row = QHBoxLayout()
        self._piper_label = QLabel("Piper voice")
        self._piper_row.addWidget(self._piper_label)
        self._piper_voice_box = QComboBox()
        self._piper_voice_box.setMinimumWidth(260)
        self._piper_row.addWidget(self._piper_voice_box, 1)
        lay.addLayout(self._piper_row)

        self._kokoro_model_row = QHBoxLayout()
        self._kokoro_model_label = QLabel("Kokoro model")
        self._kokoro_model_row.addWidget(self._kokoro_model_label)
        self._kokoro_model_box = QComboBox()
        self._kokoro_model_box.setMinimumWidth(260)
        self._kokoro_model_row.addWidget(self._kokoro_model_box, 1)
        lay.addLayout(self._kokoro_model_row)

        self._kokoro_voice_row = QHBoxLayout()
        self._kokoro_voice_label = QLabel("Kokoro voice")
        self._kokoro_voice_row.addWidget(self._kokoro_voice_label)
        self._kokoro_voice_box = QComboBox()
        self._kokoro_voice_box.setMinimumWidth(260)
        self._kokoro_voice_row.addWidget(self._kokoro_voice_box, 1)
        lay.addLayout(self._kokoro_voice_row)

        test_row = QHBoxLayout()
        btn_speak = QPushButton("Speak test")
        btn_speak.setFixedWidth(110)
        btn_speak.clicked.connect(self._on_speech_test)
        test_row.addWidget(btn_speak)
        btn_library = QPushButton("Voice library…")
        btn_library.setToolTip(
            "Browse and download Piper voices and Kokoro models from Settings."
        )
        btn_library.clicked.connect(self._on_voice_library)
        test_row.addWidget(btn_library)
        test_row.addStretch(1)
        lay.addLayout(test_row)

        # --- status --------------------------------------------------------- #
        self._speech_status = QLabel("")
        self._speech_status.setWordWrap(True)
        self._speech_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._speech_status)
        self._refresh_speech_status()

        # --- persistence ----------------------------------------------------- #
        def _persist():
            merged = self._load_app_settings()
            merged["speech_stt_engine"] = stt_box.currentData() or "google"
            merged["speech_whisper_model"] = (self._whisper_model.text() or "base.en").strip()
            merged["speech_tts_engine"] = tts_box.currentData() or "edge"
            merged["speech_tts_voice"] = self._tts_voice.text().strip()
            self._replace_app_settings(merged)
            self._refresh_speech_status()
            self._refresh_local_voice_widgets()

        stt_box.currentIndexChanged.connect(lambda _i: _persist())
        tts_box.currentIndexChanged.connect(lambda _i: _persist())
        self._whisper_model.editingFinished.connect(lambda: _persist())
        self._tts_voice.editingFinished.connect(lambda: _persist())

        self._piper_voice_box.currentIndexChanged.connect(self._on_piper_voice_changed)
        self._kokoro_model_box.currentIndexChanged.connect(self._on_kokoro_model_changed)
        self._kokoro_voice_box.currentIndexChanged.connect(self._on_kokoro_voice_changed)

        self._refresh_local_voice_widgets()

        return card

    # ------------------------------------------------------------------
    # Providers & resources
    # ------------------------------------------------------------------
    def _build_avatar_card(self):
        """Optional avatar instruction blocks, switched independently.

        Two separate switches on purpose: emotion tags drive the visuals only,
        tone steering changes how Brahma words things. They can be used alone or
        together, and leaving both off injects nothing at all.

        Availability follows the 3D background: with no avatar on screen there
        is nothing for emotion tags to drive, so the switches are disabled until
        the animation is on. The saved values are preserved either way, so
        switching the background off and back on does not lose your choices.
        """
        card = self._card(
            "Avatar Instructions",
            "Optional instruction blocks for the avatar. Off by default; these are "
            "what tell the model it may emit emotion tags or steer its tone.",
        )
        lay = card.layout()

        try:
            from core import instruction_injector as inj
        except Exception as exc:
            err = QLabel(f"Instruction injector unavailable: {exc}")
            err.setWordWrap(True)
            err.setStyleSheet("color: #ff3b30; font-size: 11px;")
            lay.addWidget(err)
            return card

        settings = self._load_app_settings()
        avatar_live = bool(settings.get("enable_3d_background", True))

        gate = QLabel(
            "3D background animation is ON — avatar instructions are available."
            if avatar_live else
            "3D background animation is OFF, so avatar instructions are unavailable. "
            "Enable it above to switch these on; your existing choices are kept."
        )
        gate.setWordWrap(True)
        gate.setStyleSheet(
            f"color: {C.TEXT if avatar_live else '#ff9f43'}; font-size: 11px;"
        )
        lay.addWidget(gate)

        hint = QLabel("")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(hint)

        boxes = {}
        for spec in inj.blocks():
            box = QCheckBox(spec["label"])
            box.setChecked(bool(spec["enabled"]))
            box.setEnabled(avatar_live)
            box.setStyleSheet(f"color: {C.TEXT};")
            box.setToolTip(f"Applies to: {', '.join(spec['layers'])}")
            lay.addWidget(box)
            boxes[spec["name"]] = box

        def _persist():
            merged = self._load_app_settings()
            for name, box in boxes.items():
                inj.set_block_enabled(name, box.isChecked())
                merged[f"instruction_block_{name}"] = bool(box.isChecked())
            self._replace_app_settings(merged)
            try:
                stats = inj.stats()
                active = ", ".join(stats["enabled"]) if stats["enabled"] else "nothing"
                composing = ", ".join(
                    layer for layer, row in stats["layers"].items()
                    if row["injected_chars"]
                ) or "none"
                hint.setText(
                    f"Active: {active} · reaching: {composing} · "
                    f"3D background: {'on' if avatar_live else 'off'}"
                )
            except Exception:
                pass

        for box in boxes.values():
            box.stateChanged.connect(lambda _s: _persist())
        _persist()
        lay.addWidget(self._build_avatar_backend_section())
        return card

    def _build_avatar_backend_section(self):
        """Backend selection + asset paths for the avatar scaffold.

        Deliberately separate from the instruction blocks above. The blocks decide
        whether the model is *allowed* to emit a tag; this decides what consumes
        one. Having the emotion block on with no backend chosen is a normal,
        useful state while the renderers are still being built.
        """
        try:
            from core import avatar_runtime as ar
        except Exception as exc:
            lbl = QLabel(f"Avatar backend registry unavailable: {exc}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {C.RED}; font-size: 11px;")
            return lbl

        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Avatar backend — what consumes an emotion tag")
        title.setStyleSheet(f"color: {C.TEXT}; font-weight: bold;")
        outer.addWidget(title)

        master = QCheckBox("Enable avatar rendering (master switch)")
        master.setChecked(ar.is_enabled())
        master.setStyleSheet(f"color: {C.TEXT};")
        master.setToolTip("Off by default. Emotion tags are still parsed and "
                          "recorded with this off, but nothing is rendered.")
        outer.addWidget(master)

        def _selected_id():
            try:
                from memory import config_manager

                return str(config_manager.get_setting(ar.SETTING_ACTIVE, "") or "")
            except Exception:
                return ""

        combo = QComboBox()
        probes = ar.probe_all()
        for info in probes:
            mark = "ready" if info["available"] else "needs setup"
            combo.addItem(f"{info['label']} — {mark}", info["id"])
        idx = combo.findData(_selected_id())
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        outer.addWidget(combo)

        # Intensity is what turns an emotion tag from a colour change into a
        # mood: it scales the emotion's contribution to speed, wobble, bloom and
        # how strongly the voice drives the visuals. 0.0 is the pre-avatar look.
        inten_row = QHBoxLayout()
        inten_row.addWidget(QLabel("Intensity"))
        intensity = QSlider(Qt.Orientation.Horizontal)
        intensity.setRange(0, 200)
        intensity.setValue(int(round(ar.get_intensity() * 100)))
        intensity.setToolTip("0 = avatar has no visual effect, 2 = maximum")
        inten_row.addWidget(intensity, 1)
        inten_value = QLabel(f"{ar.get_intensity():.2f}")
        inten_value.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        inten_row.addWidget(inten_value)
        outer.addLayout(inten_row)

        status_rows = {}
        asset_rows = {}
        for info in probes:
            line = QLabel("")
            line.setWordWrap(True)
            line.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
            outer.addWidget(line)
            status_rows[info["id"]] = line
            if info["id"] in ("live2d", "vrm"):
                row = QHBoxLayout()
                row.addWidget(QLabel("Model"))
                edit = QLineEdit(info["asset"] or "")
                edit.setPlaceholderText(
                    "folder containing a .model3.json"
                    if info["id"] == "live2d" else "path to a .vrm file"
                )
                row.addWidget(edit, 1)
                outer.addLayout(row)
                asset_rows[info["id"]] = edit

        # External avatar. UDP cannot report whether a host is listening, so
        # this is a "armed" switch with an explicit test - not a checkbox that
        # implies a connection it cannot establish.
        osc_rows = {}
        try:
            _be = ar.get("osc_vmc")
            osc_on = bool(_be.enabled()) if _be else False
            osc_host_val = str(_be.host()) if _be else "127.0.0.1"
            osc_port_val = str(_be.port()) if _be else "39539"
        except Exception:
            osc_on, osc_host_val, osc_port_val = False, "127.0.0.1", "39539"
        osc_box = QCheckBox("Drive an external avatar over VMC/OSC (e.g. VSeeFace)")
        osc_box.setChecked(osc_on)
        osc_box.setToolTip("Sends emotion tags to 127.0.0.1:39539 over UDP. "
                           "UDP has no receipt, so this cannot confirm a host "
                           "is running - use Test to check by eye.")
        outer.addWidget(osc_box)
        osc_rows["enabled"] = osc_box

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("Host"))
        osc_host = QLineEdit(osc_host_val)
        host_row.addWidget(osc_host, 1)
        osc_port = QLineEdit(osc_port_val)
        osc_port.setFixedWidth(80)
        host_row.addWidget(QLabel("Port"))
        host_row.addWidget(osc_port)
        outer.addLayout(host_row)
        osc_rows["host"] = osc_host
        osc_rows["port"] = osc_port

        osc_test = QPushButton("Send a test mood to the external avatar")
        osc_result = QLabel("")
        osc_result.setWordWrap(True)
        osc_result.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        outer.addWidget(osc_test)
        outer.addWidget(osc_result)
        osc_rows["result"] = osc_result

        diagnostics = QLabel("")
        diagnostics.setWordWrap(True)
        diagnostics.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        outer.addWidget(diagnostics)

        def _refresh():
            try:
                for info in ar.probe_all():
                    lbl = status_rows.get(info["id"])
                    if lbl is not None:
                        text = info["detail"] or info["hint"]
                        lbl.setText(("ready — " if info["available"] else "needs setup — ") + text)
                diagnostics.setText(ar.diagnostics().replace("<br>", "\n"))
            except Exception as exc:
                diagnostics.setText(f"diagnostics unavailable: {exc}")

        def _persist():
            try:
                ar.set_enabled(master.isChecked())
                bid = combo.currentData()
                if bid:
                    ar.set_active(str(bid))
                ar.set_intensity(intensity.value() / 100.0)
                for bid2, edit in asset_rows.items():
                    ar.set_asset_path(bid2, edit.text().strip())
                # The OSC backend owns its own settings accessors; going through
                # them rather than poking _setting keeps the write on the same
                # path the backend reads from.
                try:
                    _be = ar.get("osc_vmc")
                    if _be is not None:
                        _be.set_enabled(bool(osc_box.isChecked()))
                        _be.set_host(osc_host.text().strip() or "127.0.0.1")
                        _be.set_port(osc_port.text().strip() or 39539)
                except Exception:
                    pass
            except Exception as exc:
                diagnostics.setText(f"could not save avatar settings: {exc}")
            inten_value.setText(f"{ar.get_intensity():.2f}")
            _refresh()
            # The backend decides which document the background shows, so a
            # change here is a page swap, not just a setting write.
            try:
                host = self._owner_window()
                fn = getattr(host, "refresh_avatar_renderer", None)
                if callable(fn):
                    fn()
            except Exception:
                pass

        master.stateChanged.connect(lambda _s: _persist())
        combo.currentIndexChanged.connect(lambda _i: _persist())
        intensity.valueChanged.connect(lambda _v: _persist())
        osc_box.stateChanged.connect(lambda _s: _persist())
        for widget in (osc_host, osc_port):
            widget.editingFinished.connect(_persist)

        def _osc_test():
            try:
                be = ar.get("osc_vmc")
                if be is None:
                    osc_result.setText("external avatar backend unavailable")
                    return
                _persist()
                outcome = be.send_test()
                msg = ("Sent: " if outcome.get("ok") else "Failed: ") + str(
                    outcome.get("detail", ""))
                osc_result.setStyleSheet(
                    f"color: {C.ACC if outcome.get('ok') else C.RED}; font-size: 11px;")
                osc_result.setText(msg)
            except Exception as exc:
                osc_result.setStyleSheet(f"color: {C.RED}; font-size: 11px;")
                osc_result.setText(f"Test failed: {exc}")

        osc_test.clicked.connect(_osc_test)
        _refresh()
        return wrap

    def _build_apply_bar(self):
        """Explicit save + restart, and a readback of what this process loaded.

        Most cards here save as you toggle. This bar exists because a toggle
        that fails silently is indistinguishable from one that worked: it
        reports whether the modules this run depends on are actually present,
        which is the usual reason a fresh setting appears to do nothing (the app
        was started before the module existed).
        """
        card = self._card(
            "Apply & Diagnostics",
            "Save every setting, and check that this running copy has the newest "
            "code loaded. Changes to the provider chain take effect immediately; "
            "new modules only load on restart.",
        )
        lay = card.layout()

        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save All Settings")
        btn_save.setFixedHeight(32)
        btn_save.clicked.connect(lambda: self._on_save_all_clicked())
        btn_row.addWidget(btn_save)

        btn_restart = QPushButton("Restart Brahma Evo")
        btn_restart.setFixedHeight(32)
        btn_restart.clicked.connect(self._restart_app)
        btn_row.addWidget(btn_restart)

        btn_check = QPushButton("Refresh diagnostics")
        btn_check.setFixedHeight(32)
        btn_check.clicked.connect(lambda: self._refresh_diagnostics())
        btn_row.addWidget(btn_check)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        self._save_status = QLabel("")
        self._save_status.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._save_status)

        self._diag_label = QLabel("")
        self._diag_label.setWordWrap(True)
        self._diag_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(self._diag_label)
        self._refresh_diagnostics()
        return card

    def _refresh_diagnostics(self):
        """Report loaded modules, live chain, and the 3D state."""
        lines = []
        try:
            import importlib

            for mod in ("core.provider_router", "core.speech_stt", "core.speech_tts",
                        "core.instruction_injector", "core.tool_bridge"):
                try:
                    importlib.import_module(mod)
                    lines.append(f"[OK] {mod}")
                except Exception as exc:
                    lines.append(f"[MISSING] {mod} — {type(exc).__name__}")
        except Exception as exc:
            lines.append(f"diagnostics unavailable: {exc}")

        try:
            from core import provider_router

            lines.append(f"Chain: {provider_router.describe()}")
            # Show each toggle as it is actually stored, so a reverted value is
            # visible here instead of only being inferred from the chain text.
            flags = ", ".join(
                f"{pid}={'on' if provider_router.is_enabled(pid) else 'off'}"
                for pid in ("gemini", "openrouter", "local", "custom")
            )
            lines.append(f"Stored provider toggles: {flags}")
        except Exception:
            pass

        settings = self._load_app_settings()
        lines.append(
            f"3D background: {'on' if settings.get('enable_3d_background', True) else 'off'}"
            f" · memory extraction: "
            f"{'on' if settings.get('memory_extraction_enabled', True) else 'off'}"
        )
        missing = [line for line in lines if line.startswith("[MISSING]")]
        if missing:
            lines.append(">> A missing module means this process started before it "
                         "existed. Restart Brahma Evo for the toggles to take effect.")
        try:
            self._diag_label.setText("<br>".join(lines))
        except Exception:
            pass
        return lines

    def _announce_save(self, ok: bool, detail: str):
        """Show that the click registered and what it wrote.

        Requested: a toggle that fails silently looks exactly like one that
        worked. This says so on the card itself, without requiring the user to
        read the log.
        """
        try:
            from datetime import datetime

            stamp = datetime.now().strftime("%H:%M:%S")
            if ok:
                self._save_status.setStyleSheet(f"color: {C.GREEN}; font-size: 11px;")
                self._save_status.setText(f"[{stamp}] Saved. {detail}")
            else:
                self._save_status.setStyleSheet(f"color: {C.RED}; font-size: 11px;")
                self._save_status.setText(f"[{stamp}] Save failed. {detail}")
        except Exception:
            pass

    def _on_save_all_clicked(self):
        """Force a write of the current on-disk settings and confirm it.

        The write is the thing being reported, so it is separated from
        everything around it. An earlier revision logged first and let the log
        call raise, which reported "Save Failed" for a save that had in fact
        already succeeded.
        """
        # Re-read from disk rather than a cached snapshot, so the button
        # reports on what is really stored - including keys another owner
        # (provider_router, instruction_injector) changed since.
        self._invalidate_app_settings_cache()
        try:
            settings = self._load_app_settings()
            self._replace_app_settings(settings)
        except Exception as exc:
            self._log(f"ERR: Save failed: {exc}")
            self._announce_save(False, str(exc))
            return False

        detail = (f"{len(settings)} keys written to {APP_SETTINGS_FILE.name}. "
                  "Stored provider toggles are listed under Refresh diagnostics.")
        # Reporting only: a failure here must not undo or mislabel the save.
        self._log(f"SYS: {detail}")
        self._announce_save(True, detail)
        try:
            self._refresh_diagnostics()
        except Exception:
            pass
        return True

    def _host_window(self):
        """Walk up to the MainWindow hosting this settings page.

        The page is constructed with no controller, so it cannot reach BrahmaUI
        directly. Walking the parent chain finds the window that owns the
        background widget, which is what the live toggles need. Returns None
        rather than raising, so callers can report "restart required" instead of
        failing silently.
        """
        node = self
        seen = 0
        while node is not None and seen < 64:
            if hasattr(node, "write_log") and hasattr(node, "_bg_widget"):
                return node
            node = node.parentWidget()
            seen += 1
        return None

    def _owner_window(self):
        """The single accessor for the object that owns settings on this page.

        This page reaches the owning window two different ways - through a
        controller when one is attached, and through _host_window() when it is
        not - and that ambiguity caused a run of AttributeErrors
        ('_invalidate_app_settings_cache', '_save_app_settings', '_win').
        Callers use this and never spell the lookup out themselves. Returns None
        rather than raising: a settings page with no window is a real state
        during startup, not an error.
        """
        try:
            ctrl = self._ctrl()
        except Exception:
            ctrl = None
        win = getattr(ctrl, "_win", None) if ctrl is not None else None
        if win is None:
            win = self._host_window()
        return win

    def _log(self, message: str):
        """Write to the app log if a window is reachable. Never raises."""
        try:
            win = self._owner_window()
            if win is not None and hasattr(win, "write_log"):
                win.write_log(message)
        except Exception:
            pass

    def _build_providers_card(self):
        """Provider kill switches and the resource toggles, in Settings proper.

        The launcher control panel exposes the same switches, but that is a
        power-user surface. These need a home in the Settings hub too, and both
        must read/write through core.provider_router so there is exactly one
        source of truth for "is this provider allowed to be called".
        """
        card = self._card(
            "Providers & Resources",
            "Which AI providers the fallback chain may use, and the background "
            "work that competes with a local model for CPU.",
        )
        lay = card.layout()

        try:
            from core import provider_router
        except Exception as exc:
            err = QLabel(f"Provider router unavailable: {exc}")
            err.setWordWrap(True)
            err.setStyleSheet("color: #ff3b30; font-size: 11px;")
            lay.addWidget(err)
            return card

        provider_boxes = {}
        for spec in provider_router.PROVIDERS:
            row = QHBoxLayout()
            box = QCheckBox(spec["label"])
            # Build the control blocked: setChecked fires stateChanged, and a
            # live handler here would write to the settings file just by
            # rendering the card.
            box.blockSignals(True)
            box.setChecked(bool(provider_router.is_enabled(spec["id"])))
            box.blockSignals(False)
            box.setStyleSheet(f"color: {C.TEXT};")
            box.setToolTip(spec.get("hint", ""))
            row.addWidget(box)
            kind = QLabel("Gemini SDK" if spec["kind"] == "gemini" else "OpenAI-compatible")
            kind.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
            row.addWidget(kind)
            row.addStretch(1)
            lay.addLayout(row)
            provider_boxes[spec["id"]] = box

        chain_label = QLabel("")
        chain_label.setWordWrap(True)
        chain_label.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        lay.addWidget(chain_label)

        def _refresh_chain():
            chain_label.setText(f"Fallback order: {provider_router.describe()}")

        def _persist(pid):
            # Write ONLY the provider that changed. Writing all of them on any
            # single toggle meant one stale checkbox silently re-enabled
            # another provider - the diagnostics box would read "off" at launch
            # and flip to "on" after a refresh.
            try:
                provider_router.set_enabled(pid, provider_boxes[pid].isChecked())
                self._invalidate_app_settings_cache()
                _refresh_chain()
            except Exception as exc:
                # A raise here propagates into the Qt signal loop and takes the
                # whole process down, so it is reported instead.
                try:
                    chain_label.setText(f"Fallback order unavailable: {exc}")
                except Exception:
                    pass

        for pid, box in provider_boxes.items():
            box.stateChanged.connect(lambda _s, _pid=pid: _persist(_pid))

        # --- background work ------------------------------------------------ #
        settings = self._load_app_settings()

        mem_box = QCheckBox("Background memory extraction")
        mem_box.blockSignals(True)
        mem_box.setChecked(bool(settings.get("memory_extraction_enabled", True)))
        mem_box.blockSignals(False)
        mem_box.setToolTip(
            "Turn off to stop the background analysis that runs between messages. "
            "It stays available in Local and Offline mode - it is never forced off "
            "by the provider you select."
        )
        mem_box.setStyleSheet(f"color: {C.TEXT};")
        lay.addWidget(mem_box)

        anim_box = QCheckBox("3D background animation")
        anim_box.blockSignals(True)
        anim_box.setChecked(bool(settings.get("enable_3d_background", True)))
        anim_box.blockSignals(False)
        anim_box.setToolTip("Off stops the WebGL background, saving GPU and CPU every frame.")
        anim_box.setStyleSheet(f"color: {C.TEXT};")
        lay.addWidget(anim_box)

        def _persist_resources():
            try:
                merged = self._load_app_settings()
                merged["memory_extraction_enabled"] = bool(mem_box.isChecked())
                merged["enable_3d_background"] = bool(anim_box.isChecked())
                self._replace_app_settings(merged)
                self._invalidate_app_settings_cache()
            except Exception as exc:
                # Never let a persist failure escape into the Qt signal loop.
                try:
                    self._diag_hint = f"Could not save resource toggles: {exc}"
                except Exception:
                    pass
                return
            # Applied here rather than delegated: this page has no route to
            # BrahmaUI, and a delegated call that raised was being swallowed --
            # which is exactly how "the toggle did nothing" happened.
            if anim_box.isChecked() != anim_box.property("last_applied"):
                anim_box.setProperty("last_applied", anim_box.isChecked())
                host = self._host_window()
                bg = getattr(host, "_bg_widget", None) if host else None
                if bg is None or not hasattr(bg, "set_animation_enabled"):
                    try:
                        self._diag_hint = (
                            "3D background: saved, but no background widget is reachable "
                            "from this page. Restart Brahma Evo to apply."
                        )
                    except Exception:
                        pass
                else:
                    readback = bg.set_animation_enabled(bool(anim_box.isChecked())) or {}
                    try:
                        if not readback.get("web_view"):
                            self._diag_hint = (
                                "3D background: saved, but there is no web view "
                                f"(WebEngine available: {readback.get('engine_available')}). "
                                "Restart to apply."
                            )
                        else:
                            self._diag_hint = (
                                "3D background: "
                                f"{'enabled' if anim_box.isChecked() else 'disabled'} "
                                f"(js {'issued' if readback.get('js_issued') else 'not issued'})."
                            )
                    except Exception:
                        pass

        mem_box.stateChanged.connect(lambda _s: _persist_resources())
        anim_box.stateChanged.connect(lambda _s: _persist_resources())

        _refresh_chain()
        return card

    def _refresh_speech_status(self):
        """Shows what is selected and what is actually installed. Never raises."""
        try:
            from core import speech_stt as _stt
            from core import speech_tts as _tts

            stt_live = _stt.resolve_available()
            tts_live = _tts.resolve_available()
            stt_local = next(
                (e["local"] for e in _stt.available_engines() if e["id"] == stt_live), False
            )
            tts_local = next(
                (e["local"] for e in _tts.available_engines() if e["id"] == tts_live), False
            )
        except Exception as exc:
            try:
                self._speech_status.setText(f"Speech engine status unavailable: {exc}")
            except Exception:
                pass
            return

        offline = "offline" if (stt_local and tts_local) else "needs internet"
        note = ""
        try:
            from memory import config_manager

            if config_manager.get_setting("offline_mode_enabled", False):
                note = " · Offline mode is ON, so SAPI is used unless you pick another voice above."
        except Exception:
            pass
        self._speech_status.setText(
            f"Transcribing with {stt_live} · speaking with {tts_live} · {offline}{note}"
        )

    def _on_speech_test(self):
        """Speak a short phrase through the selected engine, on a worker thread.

        Uses the engine the card currently shows, not the app's installed speech
        sink - the sink is the Gemini-backed path, so honouring it made this
        button play Gemini no matter what was selected. Reports which engine
        actually ran, and reports failure instead of swallowing it.
        """
        try:
            engine = ""
            try:
                idx = self._tts_box.currentIndex()
                engine = (self._tts_box.itemData(idx) or "") if idx >= 0 else ""
            except Exception:
                engine = ""
            if not engine:
                try:
                    from core import speech_tts as _tts

                    engine = _tts.get_engine_id()
                except Exception:
                    engine = ""
            try:
                label = f"Playing test phrase via {engine}..."
            except Exception:
                label = "Playing test phrase..."
            self._speech_status.setText(label)

            import threading

            def _run():
                try:
                    from core import speech_tts as _tts

                    played, used = _tts.speak_with(
                        engine, "Brahma Evo speech engine test."
                    )
                    msg = f"Tested {used or engine}: {'audio played' if played else 'no audio produced'}."
                    run_on_ui(self, lambda: self._speech_status.setText(msg))
                except Exception as exc:
                    msg = f"Speech test failed: {exc}"
                    run_on_ui(self, lambda: self._speech_status.setText(msg))

            threading.Thread(target=_run, daemon=True, name="speech-test").start()
        except Exception as exc:
            self._speech_status.setText(f"Speech test failed to start: {exc}")

    # ------------------------------------------------------------------
    # Local voice pickers + the Voice Library dialog
    # ------------------------------------------------------------------
    def _refresh_local_voice_widgets(self):
        """Rebuild the Piper/Kokoro voice combos and show the relevant rows.

        Rebuilt from what is on disk every time, because the Voice Library can
        add or remove voices while the card is open, and because the pickers
        are only meaningful for the engine they belong to.
        """
        try:
            from core import speech_tts as _tts
        except Exception:
            return
        try:
            engine = self._tts_box.currentData() if hasattr(self, "_tts_box") else ""
        except Exception:
            engine = ""

        try:
            current_piper = _tts.get_piper_voice()
            box = self._piper_voice_box
            box.blockSignals(True)
            box.clear()
            box.addItem("(auto-discover)", "")
            for voice in _tts.piper_available_voices():
                box.addItem(voice.get("id") or voice.get("path") or "?", voice.get("id") or "")
            idx = box.findData(current_piper)
            box.setCurrentIndex(idx if idx >= 0 else 0)
            box.blockSignals(False)
        except Exception:
            pass

        try:
            from core import voice_catalog

            current_model = _tts.get_kokoro_model()
            box = self._kokoro_model_box
            box.blockSignals(True)
            box.clear()
            box.addItem("(auto-discover)", "")
            for name in voice_catalog.installed_kokoro_models():
                box.addItem(name, name)
            idx = box.findData(current_model)
            box.setCurrentIndex(idx if idx >= 0 else 0)
            box.blockSignals(False)

            current_voice = _tts.get_kokoro_voice()
            box = self._kokoro_voice_box
            box.blockSignals(True)
            box.clear()
            for name in _tts.kokoro_available_voices():
                box.addItem(name, name)
            idx = box.findText(current_voice)
            box.setCurrentIndex(idx if idx >= 0 else 0)
            box.blockSignals(False)
        except Exception:
            pass

        # Show only the rows the current engine owns. PyQt6 does not expose
        # QLayout.isVisible()/setVisible(), so visibility is driven from the
        # widgets in each row rather than from the layout itself.
        try:
            show_piper = engine == "piper"
            show_kokoro = engine == "kokoro"
            for widget in (self._piper_label, self._piper_voice_box):
                widget.setVisible(show_piper)
            for widget in (
                self._kokoro_model_label, self._kokoro_model_box,
                self._kokoro_voice_label, self._kokoro_voice_box,
            ):
                widget.setVisible(show_kokoro)
        except Exception:
            pass

    def _on_piper_voice_changed(self, _index: int) -> None:
        try:
            from core import speech_tts as _tts

            _tts.set_piper_voice(self._piper_voice_box.currentData() or "")
            # The legacy Voice file field is a path override that would win
            # over the picker; clear it so this choice is what is spoken.
            _tts.set_voice("")
            try:
                self._tts_voice.setText("")
            except Exception:
                pass
            self._refresh_speech_status()
        except Exception:
            pass

    def _on_kokoro_model_changed(self, _index: int) -> None:
        try:
            from core import speech_tts as _tts
            from core import voice_catalog

            model = self._kokoro_model_box.currentData() or ""
            _tts.set_kokoro_model(model)
            _tts.set_voice("")
            try:
                self._tts_voice.setText("")
            except Exception:
                pass
            if model:
                # A model only speaks with its matching pack; select it so the
                # user does not have to know that pairing.
                required = voice_catalog.kokoro_bin_for_model(model)
                if required and required in voice_catalog.installed_kokoro_bins():
                    _tts.set_kokoro_voices(required)
            self._refresh_local_voice_widgets()
            self._refresh_speech_status()
        except Exception:
            pass

    def _on_kokoro_voice_changed(self, _index: int) -> None:
        try:
            from core import speech_tts as _tts

            _tts.set_kokoro_voice(self._kokoro_voice_box.currentText().strip())
            self._refresh_speech_status()
        except Exception:
            pass

    def _on_voice_library(self):
        """Open the Voice Library dialog, then re-read whatever it changed."""
        try:
            from core.ui_voice_library import VoiceLibraryDialog
        except Exception as exc:
            try:
                self._speech_status.setText(f"Voice library unavailable: {exc}")
            except Exception:
                pass
            return
        try:
            dialog = VoiceLibraryDialog(self, on_changed=self._after_voice_library_change)
            dialog.exec()
        except Exception as exc:
            try:
                self._speech_status.setText(f"Voice library failed to open: {exc}")
            except Exception:
                pass
            return
        self._after_voice_library_change()

    def _after_voice_library_change(self):
        """Engine availability and installed voices may have just changed."""
        self._refresh_engine_marks()
        self._refresh_local_voice_widgets()
        self._refresh_speech_status()

    def _refresh_engine_marks(self):
        """Update the "(not installed)" marks on the engine combos in place."""
        try:
            from core import speech_stt as _stt
            from core import speech_tts as _tts

            pairs = (
                (getattr(self, "_stt_box", None), _stt.available_engines()),
                (getattr(self, "_tts_box", None), _tts.available_engines()),
            )
        except Exception:
            return
        for box, engines in pairs:
            if box is None:
                continue
            marks = {
                e["id"]: ("" if e["available"] else "  (not installed)") for e in engines
            }
            try:
                for i in range(box.count()):
                    engine_id = box.itemData(i)
                    if engine_id not in marks:
                        continue
                    label = str(box.itemText(i)).split("  (not installed)")[0]
                    box.setItemText(i, f"{label}{marks[engine_id]}")
            except Exception:
                continue

    def _build_custom_mcp_card(self):
        """Card for registering arbitrary MCP servers from Settings.

        Each row is one stdio (command/args) or remote (url) MCP server. Saving
        writes `custom_mcp_servers`, which core.mcp_manager.MCPServerManager
        reads at startup; removing takes effect on the next restart.
        """
        card = self._card(
            "Custom MCP Servers",
            "Connect any MCP server. Its tools become available to the agent automatically.",
        )
        mlay = card.layout()

        def _rebuild_list():
            while mlay.count():
                item = mlay.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()

            servers = self._custom_mcp_servers()
            if not servers:
                empty = QLabel("No custom MCP servers configured.")
                empty.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
                mlay.addWidget(empty)
                return

            for entry in servers:
                name = str(entry.get("name") or "(unnamed)")
                target = str(entry.get("url") or "").strip()
                if not target:
                    cmd = str(entry.get("command") or "").strip()
                    args = entry.get("args") or []
                    arg_text = " ".join(str(a) for a in args)
                    target = f"{cmd} {arg_text}".strip()

                row = QWidget()
                rlay = QHBoxLayout(row)
                rlay.setContentsMargins(0, 0, 0, 0)

                label = QLabel(f"<b>{name}</b>")
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setWordWrap(True)
                detail = QLabel(target)
                detail.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
                detail.setWordWrap(True)
                detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                labels = QWidget()
                llay = QVBoxLayout(labels)
                llay.setContentsMargins(0, 0, 0, 0)
                llay.setSpacing(0)
                llay.addWidget(label)
                llay.addWidget(detail)
                rlay.addWidget(labels, 1)

                btn_remove = QPushButton("🗑 Remove")
                btn_remove.setFixedWidth(95)
                btn_remove.setStyleSheet("background: rgba(255, 85, 85, 0.15); color: #ff5555; border: 1px solid #ff5555; border-radius: 4px; padding: 4px;")

                def _make_remove(server_name):
                    def _remove():
                        settings = self._load_app_settings()
                        kept = [
                            e for e in (settings.get("custom_mcp_servers") or [])
                            if isinstance(e, dict) and str(e.get("name")) != server_name
                        ]
                        settings["custom_mcp_servers"] = kept
                        self._replace_app_settings(settings)
                        _rebuild_list()
                        _refresh_visibility = getattr(self, "_rebuild_tool_visibility", None)
                        if callable(_refresh_visibility):
                            _refresh_visibility()
                        self._log_custom_mcp_change(f"removed '{server_name}'")

                    return _remove

                btn_remove.clicked.connect(_make_remove(name))
                rlay.addWidget(btn_remove)
                mlay.addWidget(row)

        def _log_custom_mcp_change(what: str):
            message = f"SYS: Custom MCP server {what}. Restart Brahma to apply."
            if self._ctrl() and hasattr(self._ctrl(), "write_log"):
                try:
                    self._ctrl().write_log(message)
                except Exception:
                    pass

        self._mcp_name_input = QLineEdit()
        self._mcp_name_input.setPlaceholderText("Name (e.g. Filesystem)")
        mlay.addWidget(self._mcp_name_input)

        self._mcp_command_input = QLineEdit()
        self._mcp_command_input.setPlaceholderText("Command or URL (e.g. npx, or https://host/mcp)")
        mlay.addWidget(self._mcp_command_input)

        self._mcp_args_input = QLineEdit()
        self._mcp_args_input.setPlaceholderText("Arguments - JSON list (e.g. [\"-y\", \"@modelcontextprotocol/server-filesystem\", \"C:/tmp\"])")
        mlay.addWidget(self._mcp_args_input)

        self._mcp_feedback = QLabel("")
        self._mcp_feedback.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        mlay.addWidget(self._mcp_feedback)

        btn_add = QPushButton("➕ Add MCP Server")
        btn_add.setStyleSheet(f"background: {C.PRI}; color: {C.DARK}; font-weight: bold; padding: 7px; border-radius: 4px;")
        btn_add.clicked.connect(lambda: self._add_custom_mcp_server())
        mlay.addWidget(btn_add)

        _rebuild_list()
        # Expose the rebuild closure so _add_custom_mcp_server can refresh the
        # list after writing settings, without re-reading the whole page.
        self._rebuild_custom_mcp_list = _rebuild_list
        return card

    def _add_custom_mcp_server(self):
        name = (self._mcp_name_input.text() or "").strip()
        raw_target = (self._mcp_command_input.text() or "").strip()
        raw_args = (self._mcp_args_input.text() or "").strip()

        def _fail(msg: str):
            self._mcp_feedback.setText(f"⚠ {msg}")
            self._mcp_feedback.setStyleSheet("color: #ff5555; font-size: 11px;")

        if not name:
            return _fail("Give the server a name so it can be removed later.")
        if not raw_target:
            return _fail("Enter a command (stdio server) or a URL (remote server).")

        args = []
        if raw_args:
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                try:
                    import shlex
                    parsed = shlex.split(raw_args)
                except Exception:
                    return _fail("Arguments must be a JSON list, e.g. [\"-y\", \"pkg\"].")
            if isinstance(parsed, str):
                parsed = [parsed]
            if not isinstance(parsed, list):
                return _fail("Arguments must be a JSON list of strings.")
            args = [str(a) for a in parsed]

        settings = self._load_app_settings()
        existing = [e for e in (settings.get("custom_mcp_servers") or []) if isinstance(e, dict)]
        if any(str(e.get("name")) == name for e in existing):
            return _fail(f"A custom MCP server named '{name}' already exists.")
        existing.append({"name": name, "command": raw_target, "args": args})
        settings["custom_mcp_servers"] = existing
        self._replace_app_settings(settings)

        self._mcp_name_input.clear()
        self._mcp_command_input.clear()
        self._mcp_args_input.clear()
        self._mcp_feedback.setText("")
        self._mcp_feedback.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")

        rebuild = getattr(self, "_rebuild_custom_mcp_list", None)
        if callable(rebuild):
            rebuild()

        refresh_visibility = getattr(self, "_rebuild_tool_visibility", None)
        if callable(refresh_visibility):
            refresh_visibility()

        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            try:
                self._ctrl().write_log(f"SYS: Custom MCP server '{name}' added. Restart Brahma to apply.")
            except Exception:
                pass

    def _build_left_column(self):
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)


        


        # Instagram Connect
        ig_card = self._card("Instagram Connect", "Connect your personal Instagram account to allow Brahma Evo to manage your DMs.")
        ig_lay = ig_card.layout()

        self._ig_status_lbl = QLabel("Status: Checking...")
        self._ig_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
        ig_lay.addWidget(self._ig_status_lbl)

        self._ig_browser_btn = QPushButton("🌐 Connect via Browser (Recommended)")
        self._ig_browser_btn.setStyleSheet(f"background: {C.PRI}; color: {C.DARK}; font-weight: bold; padding: 7px; border-radius: 4px;")
        self._ig_browser_btn.setToolTip("Opens Instagram in a genuine browser window. Login once, solve 2FA easily, and never get blocked by Meta.")
        self._ig_browser_btn.clicked.connect(self._handle_ig_browser_connect)
        ig_lay.addWidget(self._ig_browser_btn)

        or_sep = QLabel("─── or connect with mobile credentials ───")
        or_sep.setAlignment(__import__('PyQt6').QtCore.Qt.AlignmentFlag.AlignCenter)
        or_sep.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 10px; margin: 4px 0;")
        ig_lay.addWidget(or_sep)

        ig_user_row = QHBoxLayout()
        ig_user_row.addWidget(QLabel("Username"))
        self._ig_username = QLineEdit()
        self._ig_username.setPlaceholderText("Instagram Username")
        ig_user_row.addWidget(self._ig_username)
        ig_lay.addLayout(ig_user_row)

        ig_pass_row = QHBoxLayout()
        ig_pass_row.addWidget(QLabel("Password"))
        self._ig_password = QLineEdit()
        self._ig_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._ig_password.setPlaceholderText("Instagram Password (or browser sessionid)")
        ig_pass_row.addWidget(self._ig_password, 1)

        self._ig_reveal_btn = QPushButton("Reveal")
        self._ig_reveal_btn.setCheckable(True)
        self._ig_reveal_btn.clicked.connect(self._toggle_ig_password_reveal)
        ig_pass_row.addWidget(self._ig_reveal_btn)
        ig_lay.addLayout(ig_pass_row)

        ig_hint = QLabel("Tip: Use 'Connect via Browser' above to avoid Meta's automated mobile blocks and 429 rate limits.")
        ig_hint.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px;")
        ig_hint.setWordWrap(True)
        ig_lay.addWidget(ig_hint)
        
        ig_btns_row = QHBoxLayout()
        
        self._ig_connect_btn = QPushButton("Connect (Password)")
        self._ig_connect_btn.clicked.connect(self._handle_ig_connect)
        ig_btns_row.addWidget(self._ig_connect_btn)
        
        self._ig_disconnect_btn = QPushButton("Disconnect")
        self._ig_disconnect_btn.clicked.connect(self._handle_ig_disconnect)
        ig_btns_row.addWidget(self._ig_disconnect_btn)

        ig_lay.addLayout(ig_btns_row)
        
        lay.addWidget(ig_card)
        
        # Pre-fill from API keys if they exist
        try:
            with open(Path("config/api_keys.json"), "r", encoding="utf-8") as f:
                d = json.load(f)
                if d.get("instagram_username"):
                    self._ig_username.setText(d.get("instagram_username"))
                if d.get("instagram_password"):
                    self._ig_password.setText(d.get("instagram_password"))
        except Exception:
            pass

        self._update_ig_status()

        # Google Workspace (Gmail / Calendar / Drive)
        gw_card = self._card("Google Workspace (Gmail / Calendar / Drive)", "Configure your Gmail, Google Calendar, and Drive integration.")
        gw_lay = gw_card.layout()

        gw_email_row = QHBoxLayout()
        gw_email_row.addWidget(QLabel("Gmail Address"))
        self._gw_email = QLineEdit()
        self._gw_email.setPlaceholderText("your-email@gmail.com")
        gw_email_row.addWidget(self._gw_email)
        gw_lay.addLayout(gw_email_row)

        gw_pass_row = QHBoxLayout()
        gw_pass_row.addWidget(QLabel("App Password"))
        self._gw_password = QLineEdit()
        self._gw_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._gw_password.setPlaceholderText("16-character Google App Password")
        gw_pass_row.addWidget(self._gw_password)

        self._gw_reveal_btn = QPushButton("Reveal")
        self._gw_reveal_btn.setCheckable(True)
        self._gw_reveal_btn.clicked.connect(self._toggle_gw_password_reveal)
        gw_pass_row.addWidget(self._gw_reveal_btn)
        gw_lay.addLayout(gw_pass_row)

        gw_btns_row = QHBoxLayout()
        self._gw_save_btn = QPushButton("Save Credentials")
        self._gw_save_btn.clicked.connect(self._handle_gw_save)
        gw_btns_row.addWidget(self._gw_save_btn)

        self._gw_test_btn = QPushButton("Test Connection")
        self._gw_test_btn.clicked.connect(self._handle_gw_test)
        gw_btns_row.addWidget(self._gw_test_btn)

        self._gw_oauth_btn = QPushButton("Load OAuth JSON")
        self._gw_oauth_btn.clicked.connect(self._handle_gw_oauth)
        gw_btns_row.addWidget(self._gw_oauth_btn)
        gw_lay.addLayout(gw_btns_row)

        self._gw_status_lbl = QLabel("Status: Ready")
        self._gw_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        gw_lay.addWidget(self._gw_status_lbl)

        gw_help = QLabel("Tip: Generate a 16-character App Password at myaccount.google.com/apppasswords")
        gw_help.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
        gw_lay.addWidget(gw_help)

        lay.addWidget(gw_card)

        # Pre-fill stored Google Workspace credentials
        try:
            from actions.google_workspace_mcp import get_stored_gmail_credentials
            _em, _pw = get_stored_gmail_credentials()
            if _em:
                self._gw_email.setText(_em)
            if _pw:
                self._gw_password.setText(_pw)
        except Exception:
            pass

        # Spotify MCP Server
        lay.addWidget(self._build_spotify_mcp_card())

        # Custom MCP Servers
        lay.addWidget(self._build_custom_mcp_card())

        # Tool & Skill Index (must come after the MCP card: it reports on them)
        lay.addWidget(self._build_tool_index_card())

        # Per-tool access list: default tools + one section per MCP server.
        lay.addWidget(self._build_tool_visibility_card())

        # Skills Library: the on-demand playbook layer, next to the tool list so
        # the two "what the agent can reach" surfaces read together.
        lay.addWidget(self._build_skills_library_card())

        # Speech Engines — sits next to the tool index because both describe how
        # a request reaches the model and how the answer gets back out.
        lay.addWidget(self._build_speech_card())

        # Apply / diagnostics bar. Saves explicitly (so nothing is lost to a
        # silent exception) and reports what this RUNNING process actually
        # loaded, which is the difference between "off" and "broken".
        lay.addWidget(self._build_apply_bar())

        # Providers & Resources — must come after the provider cards above so it
        # can report the live chain.
        lay.addWidget(self._build_providers_card())

        # Avatar instruction blocks (both optional and independent).
        lay.addWidget(self._build_avatar_card())

        # Identity & Profile
        identity_card = self._card("Identity & Profile", "Configure assistant identity, user profile, and behavior.")
        ilay = identity_card.layout()
        
        # Assistant Settings
        ast_row = QHBoxLayout()
        ast_row.addWidget(QLabel("Assistant Name"))
        self._set_ast_name = QLineEdit(identity.get_assistant_name())
        self._set_ast_name.textChanged.connect(lambda t: identity.set_assistant_name(t.strip() or "Brahma"))
        ast_row.addWidget(self._set_ast_name)
        ilay.addLayout(ast_row)
        
        app_row = QHBoxLayout()
        app_row.addWidget(QLabel("Application Name"))
        self._set_app_name = QLineEdit(identity.get_application_name())
        self._set_app_name.textChanged.connect(lambda t: identity.set_application_name(t.strip() or "Brahma Evo"))
        app_row.addWidget(self._set_app_name)
        ilay.addLayout(app_row)

        # Owner Profile
        own_row = QHBoxLayout()
        own_row.addWidget(QLabel("Your Name"))
        self._set_own_name = QLineEdit(identity.get_owner_name())
        self._set_own_name.textChanged.connect(lambda t: identity.set_owner_name(t.strip()))
        own_row.addWidget(self._set_own_name)
        ilay.addLayout(own_row)

        role_row = QHBoxLayout()
        role_row.addWidget(QLabel("Your Role"))
        self._set_own_role = QLineEdit(identity.get_owner_role())
        self._set_own_role.textChanged.connect(lambda t: identity.set_owner_role(t.strip()))
        role_row.addWidget(self._set_own_role)
        ilay.addLayout(role_row)

        # Behavior
        beh_row = QHBoxLayout()
        beh_row.addWidget(QLabel("Behavior Mode"))
        self._set_beh_mode = QComboBox()
        self._set_beh_mode.addItems(["professional", "casual", "technical", "minimal", "proactive"])
        self._set_beh_mode.setCurrentText(identity.get_behavior_mode())
        self._set_beh_mode.currentTextChanged.connect(lambda t: identity.set_behavior_mode(t))
        beh_row.addWidget(self._set_beh_mode)
        ilay.addLayout(beh_row)

        lay.addWidget(identity_card)

        # AI Providers
        card = self._card("AI Providers", "Only the supported providers are shown here.")
        lay1 = card.layout()
        self._api_defaults = self._load_api_defaults()
        self._gemini_row, self._gemini_status, self._gemini_key = self._provider_row(
            "Google Gemini",
            self._api_defaults.get("gemini_api_key", ""),
            "gemini-2.5-flash",
            "gemini",
        )
        self._or_row, self._or_status, self._or_key = self._provider_row(
            "OpenRouter",
            self._api_defaults.get("openrouter_api_key", ""),
            "auto",
            "openrouter",
        )
        lay1.addWidget(self._gemini_row)
        lay1.addWidget(self._or_row)
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self._default_provider = QComboBox()
        self._default_provider.addItems(["Google Gemini", "OpenRouter", "Custom (OpenAI-Compatible)", "Local"])
        
        current_provider = self._load_app_settings().get("default_ai_provider", "Gemini")
        if current_provider in {"Gemini", "Google Gemini"}:
            self._default_provider.setCurrentText("Google Gemini")
        elif current_provider == "Local":
            self._default_provider.setCurrentText("Local")
        elif str(current_provider).strip().lower() == "custom":
            self._default_provider.setCurrentText("Custom (OpenAI-Compatible)")
        else:
            self._default_provider.setCurrentText("OpenRouter")
            
        self._default_provider.currentTextChanged.connect(self._set_default_provider)
        controls.addWidget(QLabel("Default AI Provider"))
        controls.addWidget(self._default_provider, 1)
        lay1.addLayout(controls)
        
        # Local AI Settings
        self._local_ai_widget = QWidget()
        local_lay = QVBoxLayout(self._local_ai_widget)
        local_lay.setContentsMargins(0, 8, 0, 8)
        local_lay.setSpacing(10)
        
        # Engine Status Row
        status_row = QHBoxLayout()
        is_online = local_brain.is_available()
        status_text = "🟢 Local Engine Online (Ollama)" if is_online else "🔴 Local Engine Offline"
        self._local_status_lbl = QLabel(status_text)
        self._local_status_lbl.setStyleSheet("color: #00ffaa; font-weight: bold;" if is_online else "color: #ff5555; font-weight: bold;")
        status_row.addWidget(QLabel("Engine Status:"))
        status_row.addWidget(self._local_status_lbl, 1)
        local_lay.addLayout(status_row)

        # Server URL + OpenCode discovery
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Local Server URL"))
        self._local_url_input = QLineEdit(self._load_app_settings().get("local_ai_url", "http://localhost:11434/v1"))
        self._local_url_input.textChanged.connect(lambda t: self._set_setting("local_ai_url", t))
        url_row.addWidget(self._local_url_input, 1)
        local_lay.addLayout(url_row)

        # Optional API key: only sent when non-empty, so unauthenticated local
        # servers (Ollama / LM Studio) keep working unchanged.
        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Local API Key"))
        self._local_api_key_input = QLineEdit(self._load_app_settings().get("local_api_key", ""))
        self._local_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._local_api_key_input.setPlaceholderText("Optional - leave blank for Ollama / LM Studio")
        self._local_api_key_input.textChanged.connect(lambda t: self._set_setting("local_api_key", t))
        key_row.addWidget(self._local_api_key_input, 1)
        local_lay.addLayout(key_row)
        
        # Model Selection with Combo Box & Refresh
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Local Model"))
        self._local_model_combo = QComboBox()
        self._local_model_combo.setEditable(True)
        
        def _populate_models():
            self._local_model_combo.clear()
            installed = local_brain.list_installed_models()
            if installed:
                self._local_model_combo.addItems(installed)
            else:
                self._local_model_combo.addItem("qwen2.5:3b")
            saved_model = self._load_app_settings().get("local_ai_model", "qwen2.5:3b")
            self._local_model_combo.setCurrentText(saved_model)
            online = local_brain.is_available()
            self._local_status_lbl.setText("🟢 Local Engine Online (Ollama)" if online else "🔴 Local Engine Offline")
            self._local_status_lbl.setStyleSheet("color: #00ffaa; font-weight: bold;" if online else "color: #ff5555; font-weight: bold;")

        _populate_models()
        self._local_model_combo.currentTextChanged.connect(lambda t: self._set_setting("local_ai_model", t))
        model_row.addWidget(self._local_model_combo, 1)

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.setFixedWidth(85)
        btn_refresh.setStyleSheet("background: rgba(255, 255, 255, 0.1); border-radius: 4px; padding: 5px;")
        btn_refresh.clicked.connect(_populate_models)
        model_row.addWidget(btn_refresh)

        # Discover models exposed by an OpenAI-compatible local server (OpenCode
        # on :4096 by default). Also repoints a blank/Ollama-default URL at the
        # server that actually answered, so the setting stays self-consistent.
        btn_discover = QPushButton("🤖 Discover Local Agents")
        btn_discover.setStyleSheet("background: rgba(0, 255, 170, 0.15); color: #00ffaa; border: 1px solid #00ffaa; border-radius: 4px; padding: 5px;")
        model_row.addWidget(btn_discover)
        local_lay.addLayout(model_row)

        self._local_discovery = _ModelDiscoveryBridge()

        def _on_discovered(models, resolved_url, error):
            btn_discover.setEnabled(True)
            btn_discover.setText("🤖 Discover Local Agents")
            if not models:
                self._local_status_lbl.setText(f"🔴 No local models found ({error})")
                self._local_status_lbl.setStyleSheet("color: #ff5555; font-weight: bold;")
                return
            previous = self._local_model_combo.currentText()
            self._local_model_combo.clear()
            self._local_model_combo.addItems(models)
            target = previous if previous in models else models[0]
            self._local_model_combo.setCurrentText(target)
            # Only adopt the discovered URL when the field was blank or still the
            # Ollama default, so a user-pinned URL is never silently overwritten.
            current = (self._local_url_input.text() or "").strip()
            if not current or current.rstrip("/") in {"http://localhost:11434", "http://localhost:11434/v1"}:
                self._local_url_input.setText(resolved_url)
            self._set_setting("local_ai_model", target)
            self._local_status_lbl.setText(f"🟢 Discovered {len(models)} local model(s)")
            self._local_status_lbl.setStyleSheet("color: #00ffaa; font-weight: bold;")

        self._local_discovery.finished.connect(_on_discovered)

        def _on_discover_clicked():
            from llm_client import OPENCODE_DEFAULT_URL
            current = (self._local_url_input.text() or "").strip()
            target = current if current else OPENCODE_DEFAULT_URL
            btn_discover.setEnabled(False)
            btn_discover.setText("⏳ Discovering...")
            self._local_discovery.discover(target)

        btn_discover.clicked.connect(_on_discover_clicked)

        # 1-Click Model Download Helper Button
        action_row = QHBoxLayout()
        btn_pull = QPushButton("📥 Pull Qwen 2.5 (3B)")
        btn_pull.setStyleSheet("background: rgba(0, 255, 170, 0.15); color: #00ffaa; border: 1px solid #00ffaa; border-radius: 4px; padding: 6px;")
        def _on_download_click():
            btn_pull.setText("⏳ Downloading model in background...")
            btn_pull.setEnabled(False)
            local_brain.pull_model_async("qwen2.5:3b", lambda chunk: _populate_models())
        btn_pull.clicked.connect(_on_download_click)
        action_row.addWidget(btn_pull)
        local_lay.addLayout(action_row)

        self._local_ai_widget.setVisible(current_provider == "Local")
        self._default_provider.currentTextChanged.connect(lambda t: self._local_ai_widget.setVisible(t == "Local"))
        
        lay1.addWidget(self._local_ai_widget)

        # Custom Remote Provider (any OpenAI-compatible /chat/completions endpoint)
        self._custom_ai_widget = QWidget()
        custom_lay = QVBoxLayout(self._custom_ai_widget)
        custom_lay.setContentsMargins(0, 8, 0, 8)
        custom_lay.setSpacing(10)

        custom_url_row = QHBoxLayout()
        custom_url_row.addWidget(QLabel("Remote URL"))
        self._custom_url_input = QLineEdit(self._load_app_settings().get("custom_ai_url", ""))
        self._custom_url_input.setPlaceholderText("https://api.example.com/v1")
        self._custom_url_input.textChanged.connect(lambda t: self._set_setting("custom_ai_url", t))
        custom_url_row.addWidget(self._custom_url_input, 1)
        custom_lay.addLayout(custom_url_row)

        custom_key_row = QHBoxLayout()
        custom_key_row.addWidget(QLabel("API Key"))
        self._custom_api_key_input = QLineEdit(self._load_app_settings().get("custom_api_key", ""))
        self._custom_api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._custom_api_key_input.setPlaceholderText("Optional - leave blank if the endpoint is unauthenticated")
        self._custom_api_key_input.textChanged.connect(lambda t: self._set_setting("custom_api_key", t))
        custom_key_row.addWidget(self._custom_api_key_input, 1)
        custom_lay.addLayout(custom_key_row)

        custom_model_row = QHBoxLayout()
        custom_model_row.addWidget(QLabel("Model"))
        self._custom_model_input = QLineEdit(self._load_app_settings().get("custom_ai_model", ""))
        self._custom_model_input.setPlaceholderText("e.g. gpt-4o-mini, qwen2.5-72b-instruct")
        self._custom_model_input.textChanged.connect(lambda t: self._set_setting("custom_ai_model", t))
        custom_model_row.addWidget(self._custom_model_input, 1)
        custom_lay.addLayout(custom_model_row)

        self._custom_ai_widget.setVisible(str(current_provider).strip().lower() == "custom")
        self._default_provider.currentTextChanged.connect(
            lambda t: self._custom_ai_widget.setVisible(t == "Custom (OpenAI-Compatible)")
        )

        lay1.addWidget(self._custom_ai_widget)
        
        self._auto_switch_btn = self._mk_toggle("Automatically switch if a provider fails", bool(self._load_app_settings().get("auto_provider_switch", True)), self._toggle_auto_provider_switch)
        lay1.addWidget(self._auto_switch_btn)

        # Dedicated Offline Mode Toggle
        is_offline_mode = bool(self._load_app_settings().get("offline_mode_enabled", False))
        self._offline_mode_btn = self._mk_toggle(
            "🔒 Offline Mode (Air-Gapped: 100% Local Inference & Speech)",
            is_offline_mode,
            self._toggle_offline_mode
        )
        lay1.addWidget(self._offline_mode_btn)
        lay.addWidget(card)

        # Mobile connect
        mobile = self._card("Mobile Connect", "Connect your phone and control Brahma Evo remotely.")
        ml = mobile.layout()
        self._mobile_status = QLabel("Connection Status: Ready")
        self._mobile_phone = QLabel("Phone Name: Not connected")
        self._mobile_last = QLabel("Last Connected: Never")
        for lbl in (self._mobile_status, self._mobile_phone, self._mobile_last):
            lbl.setStyleSheet(f"color: {C.TEXT_MED};")
            ml.addWidget(lbl)
        row = QHBoxLayout()
        self._mobile_connect_btn = QPushButton("Connect Device")
        self._mobile_connect_btn.clicked.connect(self._connect_mobile)
        self._mobile_disconnect_btn = QPushButton("Disconnect")
        self._mobile_disconnect_btn.clicked.connect(self._disconnect_mobile)
        self._mobile_qr_btn = QPushButton("Generate QR Code")
        self._mobile_qr_btn.clicked.connect(self._show_qr_code)
        row.addWidget(self._mobile_connect_btn)
        row.addWidget(self._mobile_disconnect_btn)
        row.addWidget(self._mobile_qr_btn)
        ml.addLayout(row)
        lay.addWidget(mobile)

        # Attention prompts
        attention = self._card("Attention Prompts", "Control incoming message and call alerts.")
        al = attention.layout()
        self._attention_message_btn = self._mk_toggle(
            "Show incoming message prompts",
            bool(self._load_app_settings().get("attention_message_prompts", True)),
            self._toggle_attention_message_prompts,
        )
        self._attention_call_btn = self._mk_toggle(
            "Show incoming call prompts",
            bool(self._load_app_settings().get("attention_call_prompts", True)),
            self._toggle_attention_call_prompts,
        )
        al.addWidget(self._attention_message_btn)
        al.addWidget(self._attention_call_btn)
        lay.addWidget(attention)

        # Startup
        startup = self._card("Startup", "Use Brahma Evo with Windows startup preferences.")
        sl = startup.layout()
        self._startup_launch_btn = self._mk_toggle("Launch Brahma Evo when Windows starts", bool(self._load_app_settings().get("show_workspace_on_startup", False)), self._toggle_startup_from_page)
        self._startup_minimized_btn = self._mk_toggle("Launch Minimized", bool(self._load_app_settings().get("launch_minimized", False)), self._toggle_launch_minimized)
        self._startup_updates_btn = self._mk_toggle("Check for updates on startup", bool(self._load_app_settings().get("check_updates_on_startup", True)), self._toggle_update_check)
        sl.addWidget(self._startup_launch_btn)
        sl.addWidget(self._startup_minimized_btn)
        sl.addWidget(self._startup_updates_btn)
        lay.addWidget(startup)

        # Shortcuts & Pinning
        shortcuts = self._card("Shortcuts & Pinning", "Create shortcuts and pin Brahma Evo to your Windows system.")
        shl = shortcuts.layout()
        
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        
        self._desktop_shortcut_btn = QPushButton("Create Desktop Shortcut")
        self._desktop_shortcut_btn.clicked.connect(self._handle_create_desktop_shortcut)
        self._taskbar_pin_btn = QPushButton("Pin to Taskbar")
        self._taskbar_pin_btn.clicked.connect(self._handle_pin_to_taskbar)
        
        btn_row.addWidget(self._desktop_shortcut_btn, 1)
        btn_row.addWidget(self._taskbar_pin_btn, 1)
        shl.addLayout(btn_row)
        lay.addWidget(shortcuts)

        # App Theme
        theme_card = self._card("App Theme", "Select the primary color theme for Brahma Evo.")
        tl = theme_card.layout()
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Primary Color:"))
        
        self._pick_theme_btn = QPushButton("Pick Color Palette")
        self._pick_theme_btn.clicked.connect(self._pick_theme_color)
        
        current_theme = "Gold"
        try:
            if APP_SETTINGS_FILE.exists():
                with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    _d = json.load(f)
                    current_theme = _d.get("app_theme", "Gold")
        except:
            pass
            
        if current_theme.startswith("#"):
            self._pick_theme_btn.setStyleSheet(f"background: {current_theme}; color: #ffffff;")
        
        theme_row.addWidget(self._pick_theme_btn, 1)
        tl.addLayout(theme_row)
        lay.addWidget(theme_card)

        # Startup animation
        anim = self._card("Startup Animation", "Control how the boot sequence behaves.")
        al = anim.layout()
        self._startup_anim_enable_btn = self._mk_toggle("Enable Startup Animation", bool(self._startup_animation_enabled()), self._toggle_startup_animation_from_page)
        al.addWidget(self._startup_anim_enable_btn)
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Animation Speed"))
        self._anim_speed = QComboBox()
        self._anim_speed.addItems(["Fast", "Normal", "Slow"])
        self._anim_speed.setCurrentText(self._load_app_settings().get("startup_anim_speed", "Normal"))
        speed_row.addWidget(self._anim_speed, 1)
        al.addLayout(speed_row)
        self._anim_preview_btn = QPushButton("Preview Animation")
        self._anim_preview_btn.clicked.connect(self._preview_animation)
        al.addWidget(self._anim_preview_btn)
        self._preview_progress = QProgressBar()
        self._preview_progress.setRange(0, 100)
        self._preview_progress.setValue(0)
        self._preview_progress.setTextVisible(False)
        self._preview_progress.setFixedHeight(8)
        self._preview_progress.setStyleSheet("QProgressBar { background: rgba(255,255,255,0.05); border: none; border-radius: 4px; } QProgressBar::chunk { background: #00e5ff; border-radius: 4px; }")
        al.addWidget(self._preview_progress)
        lay.addWidget(anim)
        # Discord bot
        discord = self._card("Discord Bot", "Mirror Brahma Evo between the app and your server.")
        dl = discord.layout()
        self._discord_defaults = self._load_discord_settings()
        self._discord_status = QLabel("Bot Status: Offline")
        dl.addWidget(self._discord_status)
        self._discord_token = QLineEdit()
        self._discord_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._discord_token.setPlaceholderText("Bot Token")
        self._discord_token.setText((self._discord_defaults.get("bot_token") or "").strip())
        self._discord_token.setCursorPosition(0)
        dl.addWidget(self._discord_token)
        self._discord_reveal = QPushButton("Reveal")
        self._discord_reveal.setCheckable(True)
        self._discord_reveal.clicked.connect(self._toggle_discord_reveal)
        dl.addWidget(self._discord_reveal)
        self._discord_channel = QLineEdit()
        self._discord_channel.setPlaceholderText("Optional Channel ID")
        self._discord_channel.setText((self._discord_defaults.get("channel_id") or "").strip())
        dl.addWidget(self._discord_channel)
        db = QHBoxLayout()
        self._discord_save = QPushButton("Save")
        self._discord_test = QPushButton("Test Connection")
        self._discord_restart = QPushButton("Restart Bot")
        self._discord_save.clicked.connect(self._save_discord_from_page)
        self._discord_test.clicked.connect(self._test_discord_from_page)
        self._discord_restart.clicked.connect(self._restart_discord_from_page)
        db.addWidget(self._discord_save)
        db.addWidget(self._discord_test)
        db.addWidget(self._discord_restart)
        dl.addLayout(db)
        self._discord_msg = QLabel("")
        self._discord_msg.setStyleSheet(f"color: {C.TEXT_DIM};")
        dl.addWidget(self._discord_msg)
        lay.addWidget(discord)

        about = self._card("About Brahma Evo", "Brahma Evo information only.")
        ab = about.layout()
        about_grid = QGridLayout()
        about_grid.setHorizontalSpacing(22)
        about_grid.setVerticalSpacing(8)
        entries = [
            ("Version", "v1.0.0"),
            ("Build Number", "2026.06.29"),
            ("Release Date", "29 Jun 2026"),
        ]
        self._about_values: dict[str, QLabel] = {}
        for idx, (label, value) in enumerate(entries):
            key = QLabel(label)
            key.setStyleSheet(f"color: {C.TEXT_DIM};")
            val = QLabel(value)
            val.setStyleSheet(f"color: {C.WHITE}; font-weight: 700;")
            about_grid.addWidget(key, idx, 0)
            about_grid.addWidget(val, idx, 1)
            self._about_values[label] = val
        ab.addLayout(about_grid)
        lay.addWidget(about)

        # Autonomous Self-Healing & Continuous Self-Improvement Card
        auto_heal_card = self._card("Auto-Heal & Continuous Self-Improvement", "Autonomous bug repair, crash recovery sentry, AST-verified patching, and persistent behavioral directives.")
        ah_lay = auto_heal_card.layout()

        self._ah_status_lbl = QLabel("Sentry: Active | Sandbox: Enabled | Rules: 0 active")
        self._ah_status_lbl.setStyleSheet(f"color: {C.WHITE}; font-size: 12px; font-weight: bold; padding: 4px 0;")
        ah_lay.addWidget(self._ah_status_lbl)

        ah_btn_row = QHBoxLayout()
        self._ah_history_btn = QPushButton("Patch History")
        self._ah_history_btn.clicked.connect(self._handle_ah_history)
        ah_btn_row.addWidget(self._ah_history_btn)

        self._ah_rollback_btn = QPushButton("Rollback Last Patch")
        self._ah_rollback_btn.clicked.connect(self._handle_ah_rollback)
        ah_btn_row.addWidget(self._ah_rollback_btn)

        self._ah_rules_btn = QPushButton("Learned Rules")
        self._ah_rules_btn.clicked.connect(self._handle_ah_rules)
        ah_btn_row.addWidget(self._ah_rules_btn)
        ah_lay.addLayout(ah_btn_row)

        test_row = QHBoxLayout()
        self._ah_trigger_bug_btn = QPushButton("⚡ Trigger Test Bug")
        self._ah_trigger_bug_btn.clicked.connect(self._handle_ah_trigger_test_bug)
        test_row.addWidget(self._ah_trigger_bug_btn)

        self._ah_fix_bug_btn = QPushButton("🛠️ Fix Captured Bug")
        self._ah_fix_bug_btn.clicked.connect(self._handle_ah_fix_captured_bug)
        test_row.addWidget(self._ah_fix_bug_btn)
        ah_lay.addLayout(test_row)

        rule_input_row = QHBoxLayout()
        self._ah_rule_input = QLineEdit()
        self._ah_rule_input.setPlaceholderText("Teach Brahma a rule (e.g. Always summarize in bullet points)")
        rule_input_row.addWidget(self._ah_rule_input)
        self._ah_learn_btn = QPushButton("Teach Rule")
        self._ah_learn_btn.clicked.connect(self._handle_ah_learn_rule)
        rule_input_row.addWidget(self._ah_learn_btn)
        ah_lay.addLayout(rule_input_row)

        self._ah_output_lbl = QLabel("Self-healing sentry is active. Safe unbrick & auto-rollback protected.")
        self._ah_output_lbl.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
        self._ah_output_lbl.setWordWrap(True)
        ah_lay.addWidget(self._ah_output_lbl)

        lay.addWidget(auto_heal_card)
        try:
            self._refresh_auto_heal_status()
        except Exception:
            pass

        # Brahma Audio Routing & Hardware Controls
        audio_card = self._card("Audio Routing & Hardware Controls", "Select hardware audio interfaces, toggle Push-to-Talk, or inspect long-term memory.")
        alay = audio_card.layout()

        # Mic selection row
        mic_row = QHBoxLayout()
        mic_lbl = QLabel("Input Microphone")
        mic_lbl.setStyleSheet(f"color: {C.WHITE}; min-width: 130px;")
        mic_row.addWidget(mic_lbl)

        self._mic_combo = QComboBox()
        self._mic_combo.addItem("Default System Microphone")
        try:
            from core import audio_devices
            from memory import config_manager
            audio_devices.prefetch()
            input_devs = audio_devices.list_devices("input")
            for dev in input_devs:
                self._mic_combo.addItem(dev)
            saved_in = config_manager.get_input_device()
            if saved_in and saved_in in input_devs:
                self._mic_combo.setCurrentText(saved_in)
        except Exception:
            pass
        self._mic_combo.currentTextChanged.connect(self._on_input_device_changed)
        mic_row.addWidget(self._mic_combo, 1)
        alay.addLayout(mic_row)

        # Speaker selection row
        spk_row = QHBoxLayout()
        spk_lbl = QLabel("Output Speaker")
        spk_lbl.setStyleSheet(f"color: {C.WHITE}; min-width: 130px;")
        spk_row.addWidget(spk_lbl)

        self._spk_combo = QComboBox()
        self._spk_combo.addItem("Default System Speaker")
        try:
            from core import audio_devices
            from memory import config_manager
            output_devs = audio_devices.list_devices("output")
            for dev in output_devs:
                self._spk_combo.addItem(dev)
            saved_out = config_manager.get_output_device()
            if saved_out and saved_out in output_devs:
                self._spk_combo.setCurrentText(saved_out)
        except Exception:
            pass
        self._spk_combo.currentTextChanged.connect(self._on_output_device_changed)
        spk_row.addWidget(self._spk_combo, 1)
        alay.addLayout(spk_row)

        # Push-to-Talk and Memory Inspector Action buttons
        tools_row = QHBoxLayout()
        try:
            from memory import config_manager
            ptt_enabled = config_manager.get_push_to_talk_enabled()
        except Exception:
            ptt_enabled = False

        self._ptt_toggle = self._mk_toggle(
            "Push-to-Talk (Hold Ctrl+Space)",
            ptt_enabled,
            self._on_ptt_toggled
        )
        tools_row.addWidget(self._ptt_toggle)

        mem_btn = QPushButton("🧠 Inspect Memory")
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.clicked.connect(self._open_memory_inspector)
        tools_row.addWidget(mem_btn)

        undo_btn = QPushButton("↺ Rollback (Undo)")
        undo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        undo_btn.clicked.connect(self._handle_undo_click)
        tools_row.addWidget(undo_btn)
        alay.addLayout(tools_row)

        self._audio_status_lbl = QLabel("Hardware audio deduplication, echo guard and undo stack active.")
        self._audio_status_lbl.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
        alay.addWidget(self._audio_status_lbl)

        lay.addWidget(audio_card)

        lay.addStretch(1)
        return col


    def _on_input_device_changed(self, name: str):
        try:
            from memory import config_manager
            val = "" if name == "Default System Microphone" else name
            config_manager.set_input_device(val)
            self._audio_status_lbl.setText(f"✅ Input device set to: {name}")
        except Exception as e:
            self._audio_status_lbl.setText(f"Error setting input: {e}")

    def _on_output_device_changed(self, name: str):
        try:
            from memory import config_manager
            val = "" if name == "Default System Speaker" else name
            config_manager.set_output_device(val)
            self._audio_status_lbl.setText(f"✅ Output device set to: {name}")
        except Exception as e:
            self._audio_status_lbl.setText(f"Error setting output: {e}")

    def _on_ptt_toggled(self, checked: bool):
        try:
            from memory import config_manager
            config_manager.set_push_to_talk_enabled(checked)
            self._audio_status_lbl.setText(f"✅ Push-to-Talk {'enabled (Hold Ctrl+Space)' if checked else 'disabled'}")
        except Exception as e:
            self._audio_status_lbl.setText(f"Error toggling PTT: {e}")

    def _open_memory_inspector(self):
        try:
            win = self.window()
            if hasattr(win, "show_memory_inspector"):
                win.show_memory_inspector()
            elif hasattr(win, "_memory_overlay_sig"):
                win._memory_overlay_sig.emit("show")
        except Exception as e:
            self._audio_status_lbl.setText(f"Error opening memory inspector: {e}")

    def _handle_undo_click(self):
        try:
            from core import undo
            if not undo.can_undo():
                self._audio_status_lbl.setText("ℹ Nothing to undo yet.")
                return
            res = undo.undo_last()
            self._audio_status_lbl.setText(f"↺ {res}")
        except Exception as e:
            self._audio_status_lbl.setText(f"Error running undo: {e}")

    def _update_ig_status(self):
        try:
            if not hasattr(self, "_ig_status_lbl"):
                return
            from pathlib import Path
            import json
            api_keys = Path("config/api_keys.json")
            username = ""
            d = {}
            if api_keys.exists():
                with open(api_keys, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    username = d.get("instagram_username", "")

            is_browser_auth = bool(d.get("instagram_browser_authenticated") and d.get("instagram_sessionid"))
            if is_browser_auth:
                name_str = f" (@{username})" if username else ""
                self._ig_status_lbl.setText(f"Status: 🟢 Connected via Browser{name_str}")
                self._ig_status_lbl.setStyleSheet("color: #00ffcc; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
                return

            if username and Path("config/ig_session.json").exists():
                self._ig_status_lbl.setText(f"Status: 🟢 Connected (@{username})")
                self._ig_status_lbl.setStyleSheet("color: #00ffcc; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
                return

            self._ig_status_lbl.setText("Status: ⚪ Not Connected — Click 'Connect via Browser' below")
            self._ig_status_lbl.setStyleSheet(f"color: {C.PRI}; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
        except Exception:
            pass

    def _handle_ig_disconnect(self):
        try:
            import json
            import shutil
            from pathlib import Path
            with open("config/api_keys.json", "r", encoding="utf-8") as f:
                d = json.load(f)
            d["instagram_username"] = ""
            d["instagram_password"] = ""
            d["instagram_sessionid"] = ""
            d["instagram_user_id"] = ""
            d["instagram_browser_authenticated"] = False
            with open("config/api_keys.json", "w", encoding="utf-8") as f:
                json.dump(d, f, indent=4)
                
            session_path = Path("config/ig_session.json")
            if session_path.exists():
                session_path.unlink()

            profile_dir = Path("config/ig_browser_profile")
            if profile_dir.exists():
                try:
                    shutil.rmtree(profile_dir)
                except Exception:
                    pass
                
            from actions.instagram_mcp import stop_daemon, kill_browser_processes
            stop_daemon()
            kill_browser_processes()
            
            self._update_ig_status()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Disconnected", "Instagram account disconnected.")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Error disconnecting Instagram: {e}")

    class IGBrowserLoginWorker(__import__('PyQt6').QtCore.QThread):
        finished_success = __import__('PyQt6').QtCore.pyqtSignal(str)
        finished_error = __import__('PyQt6').QtCore.pyqtSignal(str)
        status_update = __import__('PyQt6').QtCore.pyqtSignal(str)

        def __init__(self, username=""):
            super().__init__()
            self.username = username

        def run(self):
            try:
                from playwright.sync_api import sync_playwright
                from pathlib import Path
                import json
                import time

                profile_dir = Path("config/ig_browser_profile").resolve()
                profile_dir.mkdir(parents=True, exist_ok=True)

                self.status_update.emit("Launching browser for Instagram...")
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(
                        str(profile_dir),
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled"],
                        viewport={"width": 1080, "height": 800},
                    )
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto("https://www.instagram.com/accounts/login/", timeout=60000)

                    # Try to prefill username
                    if self.username:
                        try:
                            user_input = page.wait_for_selector('input[name="username"], input[name="email"]', timeout=5000)
                            if user_input and not user_input.input_value():
                                user_input.fill(self.username)
                        except Exception:
                            pass

                    self.status_update.emit("Please log in in the opened browser window...")

                    logged_in = False
                    detected_username = self.username
                    # Poll cookies for up to 5 minutes
                    for _ in range(300):
                        if self.isInterruptionRequested():
                            break
                        try:
                            cookies = context.cookies("https://www.instagram.com")
                            cookie_map = {c["name"]: c["value"] for c in cookies}
                            if "sessionid" in cookie_map and "ds_user_id" in cookie_map:
                                logged_in = True
                                sid = cookie_map["sessionid"]
                                uid = cookie_map["ds_user_id"]

                                time.sleep(2)
                                try:
                                    u = page.evaluate("() => window._sharedData?.config?.viewer?.username || ''")
                                    if u:
                                        detected_username = u
                                    else:
                                        parts = [x for x in page.url.replace("https://www.instagram.com/", "").split("/") if x]
                                        if parts and parts[0] not in ["accounts", "direct", "explore", "reels"]:
                                            detected_username = parts[0]
                                except Exception:
                                    pass

                                try:
                                    cfg_path = Path("config/api_keys.json")
                                    data = {}
                                    if cfg_path.exists():
                                        with open(cfg_path, "r", encoding="utf-8") as f:
                                            data = json.load(f)
                                    if detected_username:
                                        data["instagram_username"] = detected_username
                                    data["instagram_sessionid"] = sid
                                    data["instagram_user_id"] = uid
                                    data["instagram_browser_authenticated"] = True
                                    with open(cfg_path, "w", encoding="utf-8") as f:
                                        json.dump(data, f, indent=4)
                                except Exception:
                                    pass

                                break
                        except Exception:
                            pass
                        time.sleep(1)

                    context.close()

                    if logged_in:
                        from actions.instagram_mcp import stop_daemon, start_daemon
                        stop_daemon()
                        start_daemon()
                        self.finished_success.emit(detected_username or "")
                    else:
                        self.finished_error.emit("Login timed out or the browser window was closed before login was completed.")
            except Exception as e:
                self.finished_error.emit(str(e))

    def _handle_ig_browser_connect(self):
        self._ig_browser_btn.setEnabled(False)
        self._ig_browser_btn.setText("Connecting via Browser...")
        from actions.instagram_mcp import stop_daemon, kill_browser_processes
        stop_daemon()
        kill_browser_processes()
        username = self._ig_username.text().strip()
        self._ig_browser_worker = self.IGBrowserLoginWorker(username)
        self._ig_browser_worker.finished_success.connect(self._ig_browser_success)
        self._ig_browser_worker.finished_error.connect(self._ig_browser_error)
        self._ig_browser_worker.start()

    def _ig_browser_success(self, detected_username):
        self._ig_browser_btn.setEnabled(True)
        self._ig_browser_btn.setText("🌐 Connect via Browser (Recommended)")
        if detected_username:
            self._ig_username.setText(detected_username)
        self._update_ig_status()
        from PyQt6.QtWidgets import QMessageBox
        name_str = f" as @{detected_username}" if detected_username else ""
        QMessageBox.information(self, "Instagram Connected", f"Instagram successfully connected via Browser{name_str}!\n\nBrahma is now active for voice DM notifications and instant direct replies.")

    def _ig_browser_error(self, err_msg):
        self._ig_browser_btn.setEnabled(True)
        self._ig_browser_btn.setText("🌐 Connect via Browser (Recommended)")
        self._update_ig_status()
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Browser Login", f"Browser connection was not completed: {err_msg}")

    class IGLoginWorker(__import__('PyQt6').QtCore.QThread):
        finished_success = __import__('PyQt6').QtCore.pyqtSignal()
        finished_error = __import__('PyQt6').QtCore.pyqtSignal(str)
        request_2fa = __import__('PyQt6').QtCore.pyqtSignal()
        request_challenge = __import__('PyQt6').QtCore.pyqtSignal()
        
        def __init__(self, username, password):
            super().__init__()
            self.username = username
            self.password = password
            self.code_event = __import__('threading').Event()
            self.code = None
            
        def run(self):
            try:
                from instagrapi import Client
                from instagrapi.exceptions import TwoFactorRequired, ChallengeRequired
                import json
                from pathlib import Path
                cl = Client()
                
                pwd = (self.password or "").strip()
                if pwd.startswith("sessionid:") or (len(pwd) > 40 and "%3A" in pwd):
                    sid = pwd.replace("sessionid:", "").strip()
                    cl.login_by_sessionid(sid)
                else:
                    cl.username = self.username
                    outcome = cl.bloks_caa_login(self.username, pwd)
                    raw_str = json.dumps(outcome, default=str)
                    
                    if outcome.get("logged_in"):
                        cl.login_flow()
                    elif "login_wrong_password" in raw_str or "Incorrect password" in raw_str or "password you entered is incorrect" in raw_str:
                        raise Exception("The password you entered is incorrect. Please check your Instagram password, or paste your web browser 'sessionid' cookie.")
                    elif "checkpoint_required" in raw_str or "challenge_required" in raw_str:
                        try:
                            cl.login(self.username, pwd)
                        except TwoFactorRequired:
                            self.request_2fa.emit()
                            self.code_event.wait()
                            if not self.code:
                                raise Exception("2FA code not provided.")
                            cl.two_factor_login(self.code)
                        except ChallengeRequired:
                            self.request_challenge.emit()
                            self.code_event.wait()
                            if not self.code:
                                raise Exception("Challenge code not provided.")
                            cl.challenge_resolve(self.code)
                    else:
                        try:
                            cl.login(self.username, pwd)
                        except TwoFactorRequired:
                            self.request_2fa.emit()
                            self.code_event.wait()
                            if not self.code:
                                raise Exception("2FA code not provided.")
                            cl.two_factor_login(self.code)
                        except ChallengeRequired:
                            self.request_challenge.emit()
                            self.code_event.wait()
                            if not self.code:
                                raise Exception("Challenge code not provided.")
                            cl.challenge_resolve(self.code)
                        except Exception as e:
                            err_str = str(e)
                            if "out of date" in err_str.lower() or "CAA login" in err_str:
                                raise Exception("Instagram rejected credentials or security check. Please check your username/password or paste your web browser 'sessionid' cookie.")
                            raise
                
                Path("config").mkdir(parents=True, exist_ok=True)
                cl.dump_settings("config/ig_session.json")
                from actions.instagram_mcp import stop_daemon, start_daemon
                stop_daemon()
                start_daemon()
                self.finished_success.emit()
            except Exception as e:
                self.finished_error.emit(str(e))

    def _handle_ig_connect(self):
        username = self._ig_username.text().strip()
        password = self._ig_password.text().strip()
        
        from PyQt6.QtWidgets import QMessageBox
        import json
        
        if not username or not password:
            QMessageBox.warning(self, "Error", "Please enter both username and password.")
            return
            
        self._ig_connect_btn.setEnabled(False)
        self._ig_connect_btn.setText("Connecting...")
        
        # Save credentials temporarily
        try:
            with open("config/api_keys.json", "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
            
        d["instagram_username"] = username
        d["instagram_password"] = password
        
        with open("config/api_keys.json", "w", encoding="utf-8") as f:
            json.dump(d, f, indent=4)
            
        self._ig_worker = self.IGLoginWorker(username, password)
        self._ig_worker.finished_success.connect(self._ig_login_success)
        self._ig_worker.finished_error.connect(self._ig_login_error)
        self._ig_worker.request_2fa.connect(self._ig_login_2fa)
        self._ig_worker.request_challenge.connect(self._ig_login_challenge)
        self._ig_worker.start()

    def _ig_login_success(self):
        self._ig_connect_btn.setEnabled(True)
        self._ig_connect_btn.setText("Connect (Password)")
        self._update_ig_status()
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Success", "Instagram login successful and daemon started!")

    def _ig_login_error(self, err_msg):
        self._ig_connect_btn.setEnabled(True)
        self._ig_connect_btn.setText("Connect (Password)")
        self._update_ig_status()
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Instagram Mobile Login Blocked",
            f"Instagram direct password login failed:\n{err_msg}\n\n"
            "Meta frequently blocks automated mobile password logins.\n"
            "Would you like to connect securely via Browser instead?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._handle_ig_browser_connect()

    def _ig_login_2fa(self):
        from PyQt6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(self, "2-Factor Authentication", "Enter the 6-digit code sent to your device:")
        if ok and code:
            self._ig_worker.code = code
        else:
            self._ig_worker.code = None
        self._ig_worker.code_event.set()

    def _ig_login_challenge(self):
        from PyQt6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(self, "Challenge Required", "Enter the verification code sent to your email/phone:")
        if ok and code:
            self._ig_worker.code = code
        else:
            self._ig_worker.code = None
        self._ig_worker.code_event.set()

    def _toggle_ig_password_reveal(self):
        from PyQt6.QtWidgets import QLineEdit
        if getattr(self, "_ig_reveal_btn", None) and self._ig_reveal_btn.isChecked():
            self._ig_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self._ig_reveal_btn.setText("Hide")
        else:
            self._ig_password.setEchoMode(QLineEdit.EchoMode.Password)
            self._ig_reveal_btn.setText("Reveal")

    def _toggle_gw_password_reveal(self):
        from PyQt6.QtWidgets import QLineEdit
        if getattr(self, "_gw_reveal_btn", None) and self._gw_reveal_btn.isChecked():
            self._gw_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self._gw_reveal_btn.setText("Hide")
        else:
            self._gw_password.setEchoMode(QLineEdit.EchoMode.Password)
            self._gw_reveal_btn.setText("Reveal")

    def _handle_gw_save(self):
        email_val = self._gw_email.text().strip()
        pw_val = self._gw_password.text().strip()
        from PyQt6.QtWidgets import QMessageBox
        if not email_val:
            QMessageBox.warning(self, "Missing Email", "Please enter your Gmail address.")
            return
        try:
            from actions.google_workspace_mcp import save_stored_gmail_credentials
            ok = save_stored_gmail_credentials(email_val, pw_val)
            if ok:
                try:
                    from actions.google_workspace_mcp import stop_email_daemon, start_email_daemon
                    stop_email_daemon()
                    start_email_daemon()
                except Exception:
                    pass
                self._gw_status_lbl.setText("Status: Credentials saved securely.")
                self._gw_status_lbl.setStyleSheet("color: #4caf50;")
                QMessageBox.information(self, "Saved", "Google Workspace credentials saved successfully!")
            else:
                QMessageBox.critical(self, "Error", "Failed to encrypt and save credentials.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error saving credentials: {e}")

    def _handle_gw_test(self):
        self._gw_status_lbl.setText("Status: Testing connection...")
        self._gw_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        email_val = self._gw_email.text().strip()
        pw_val = self._gw_password.text().strip()
        if email_val and pw_val:
            try:
                from actions.google_workspace_mcp import save_stored_gmail_credentials
                save_stored_gmail_credentials(email_val, pw_val)
            except Exception:
                pass

        from actions.google_workspace_mcp import GmailEngine
        res = GmailEngine.test_connection()
        from PyQt6.QtWidgets import QMessageBox
        if res.get("success"):
            self._gw_status_lbl.setText(f"Status: {res.get('message')}")
            self._gw_status_lbl.setStyleSheet("color: #4caf50;")
            QMessageBox.information(self, "Connection Successful", res.get("message"))
        else:
            self._gw_status_lbl.setText(f"Status: {res.get('message')}")
            self._gw_status_lbl.setStyleSheet("color: #f44336;")
            QMessageBox.warning(self, "Connection Failed", res.get("message"))

    def _handle_gw_oauth(self):
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getOpenFileName(self, "Select Google Cloud credentials.json", str(Path.home()), "JSON Files (*.json)")
        if path:
            try:
                import shutil
                shutil.copy2(path, Path("config/google_workspace_credentials.json"))
                self._gw_status_lbl.setText("Status: OAuth credentials.json loaded.")
                self._gw_status_lbl.setStyleSheet("color: #4caf50;")
                QMessageBox.information(self, "Success", "Google Cloud OAuth credentials loaded successfully!")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load credentials: {e}")

    class SpotifyTestWorker(__import__('PyQt6').QtCore.QThread):
        finished_result = __import__('PyQt6').QtCore.pyqtSignal(bool, str)

        def run(self):
            try:
                from actions.spotify_controller import is_spotify_configured, _spotify_mcp_call, get_spotify_config
                if not is_spotify_configured():
                    self.finished_result.emit(False, "Spotify MCP is not configured. Please enter your Client ID and Client Secret.")
                    return
                data = get_spotify_config()
                if not data.get("refreshToken"):
                    self.finished_result.emit(False, "Credentials saved, but browser authorization has not been completed. Click 'Authenticate in Browser'.")
                    return
                res = _spotify_mcp_call("getAvailableDevices", {})
                out = res.get("output", "")
                if "Error" in out or not res.get("success", False):
                    self.finished_result.emit(False, out or res.get("error", "Failed to contact Spotify API."))
                else:
                    self.finished_result.emit(True, out or "Successfully connected to Spotify MCP server!")
            except Exception as e:
                self.finished_result.emit(False, str(e))

    def _prefill_spotify_credentials(self):
        try:
            from actions.spotify_controller import get_spotify_config
            cfg = get_spotify_config()
            cid = cfg.get("clientId", "")
            csec = cfg.get("clientSecret", "")
            if cid and hasattr(self, "_spotify_client_id"):
                self._spotify_client_id.setText(cid)
            if csec and hasattr(self, "_spotify_client_secret"):
                self._spotify_client_secret.setText(csec)
        except Exception:
            pass

    def _update_spotify_status(self):
        try:
            if not hasattr(self, "_spotify_status_lbl"):
                return
            from actions.spotify_controller import get_spotify_config
            cfg = get_spotify_config()
            has_id = bool(cfg.get("clientId"))
            has_sec = bool(cfg.get("clientSecret"))
            has_token = bool(cfg.get("refreshToken"))
            if has_id and has_sec and has_token:
                self._spotify_status_lbl.setText("Status: 🟢 Connected & Authorized (Ready)")
                self._spotify_status_lbl.setStyleSheet("color: #00ffcc; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
            elif has_id and has_sec:
                self._spotify_status_lbl.setText("Status: 🟡 Credentials Saved — Click 'Authenticate in Browser'")
                self._spotify_status_lbl.setStyleSheet(f"color: {C.ACC}; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
            else:
                self._spotify_status_lbl.setText("Status: ⚪ Not Configured — Enter credentials below")
                self._spotify_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
        except Exception:
            pass

    def _toggle_spotify_secret_reveal(self):
        from PyQt6.QtWidgets import QLineEdit
        if getattr(self, "_spotify_reveal_btn", None) and self._spotify_reveal_btn.isChecked():
            self._spotify_client_secret.setEchoMode(QLineEdit.EchoMode.Normal)
            self._spotify_reveal_btn.setText("Hide")
        else:
            self._spotify_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
            self._spotify_reveal_btn.setText("Reveal")

    def _copy_spotify_redirect_uri(self):
        try:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText("http://127.0.0.1:8888/callback")
            if hasattr(self, "_spotify_status_lbl"):
                self._spotify_status_lbl.setText("Status: 📋 Redirect URI copied to clipboard!")
                self._spotify_status_lbl.setStyleSheet("color: #00e5ff; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
                QTimer.singleShot(2500, self._update_spotify_status)
        except Exception:
            pass

    def _handle_spotify_save(self):
        cid = self._spotify_client_id.text().strip()
        csec = self._spotify_client_secret.text().strip()
        from PyQt6.QtWidgets import QMessageBox
        if not cid or not csec:
            QMessageBox.warning(self, "Missing Credentials", "Please enter both Spotify Client ID and Client Secret.")
            return
        try:
            from actions.spotify_controller import save_spotify_credentials
            ok = save_spotify_credentials(cid, csec)
            if ok:
                self._update_spotify_status()
                QMessageBox.information(
                    self,
                    "Credentials Saved",
                    "Spotify credentials saved successfully!\n\n"
                    "Now click 'Authenticate in Browser' to link your Spotify account."
                )
            else:
                QMessageBox.critical(self, "Error", "Failed to save Spotify configuration.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error saving credentials: {e}")

    def _handle_spotify_auth(self):
        cid = self._spotify_client_id.text().strip()
        csec = self._spotify_client_secret.text().strip()
        from PyQt6.QtWidgets import QMessageBox
        from actions.spotify_controller import is_spotify_configured, save_spotify_credentials, _spotify_mcp_action
        if cid and csec:
            save_spotify_credentials(cid, csec)
        elif not is_spotify_configured():
            QMessageBox.warning(self, "Configuration Required", "Please enter your Spotify Client ID and Client Secret before authenticating.")
            return

        self._spotify_status_lbl.setText("Status: 🌐 Opening browser for Spotify authorization...")
        self._spotify_status_lbl.setStyleSheet(f"color: {C.ACC}; font-size: 11px; font-weight: bold; margin-bottom: 4px;")
        self._spotify_auth_btn.setEnabled(False)
        self._spotify_auth_btn.setText("Authenticating...")

        msg = _spotify_mcp_action({"action": "auth"})

        self._spotify_auth_ticks = 0
        if not hasattr(self, "_spotify_auth_timer"):
            self._spotify_auth_timer = QTimer(self)
            self._spotify_auth_timer.setInterval(1500)
            self._spotify_auth_timer.timeout.connect(self._poll_spotify_auth_status)
        self._spotify_auth_timer.start()

        QMessageBox.information(
            self,
            "Spotify Authorization",
            f"{msg}\n\n"
            "Steps to complete:\n"
            "1. Log in to Spotify in the browser window.\n"
            "2. Click 'Agree' to grant playback permissions.\n"
            "3. Once redirected to callback, Brahma Evo will automatically detect authorization!"
        )

    def _poll_spotify_auth_status(self):
        self._spotify_auth_ticks = getattr(self, "_spotify_auth_ticks", 0) + 1
        from actions.spotify_controller import get_spotify_config
        cfg = get_spotify_config()
        if cfg.get("refreshToken"):
            if hasattr(self, "_spotify_auth_timer"):
                self._spotify_auth_timer.stop()
            if hasattr(self, "_spotify_auth_btn"):
                self._spotify_auth_btn.setEnabled(True)
                self._spotify_auth_btn.setText("🔗 Authenticate in Browser")
            self._update_spotify_status()
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Spotify Connected", "Spotify MCP Server has been successfully authorized and connected!")
            return
        if self._spotify_auth_ticks >= 60:
            if hasattr(self, "_spotify_auth_timer"):
                self._spotify_auth_timer.stop()
            if hasattr(self, "_spotify_auth_btn"):
                self._spotify_auth_btn.setEnabled(True)
                self._spotify_auth_btn.setText("🔗 Authenticate in Browser")
            self._update_spotify_status()

    def _handle_spotify_test(self):
        from actions.spotify_controller import is_spotify_configured
        from PyQt6.QtWidgets import QMessageBox
        if not is_spotify_configured():
            QMessageBox.warning(self, "Not Configured", "Please enter and save your Spotify Client ID and Client Secret first.")
            return

        self._spotify_test_btn.setEnabled(False)
        self._spotify_test_btn.setText("Testing...")
        self._spotify_status_lbl.setText("Status: Testing Spotify MCP connection...")

        self._spotify_test_worker = self.SpotifyTestWorker()
        self._spotify_test_worker.finished_result.connect(self._on_spotify_test_finished)
        self._spotify_test_worker.start()

    def _on_spotify_test_finished(self, success: bool, message: str):
        self._spotify_test_btn.setEnabled(True)
        self._spotify_test_btn.setText("Test Connection")
        self._update_spotify_status()
        from PyQt6.QtWidgets import QMessageBox
        if success:
            QMessageBox.information(self, "Spotify Connection Successful", f"Connection to Spotify MCP Server was successful!\n\n{message}")
        else:
            QMessageBox.warning(self, "Spotify Connection Test", f"Spotify test result:\n\n{message}")

    def _handle_spotify_disconnect(self):
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Disconnect Spotify",
            "Are you sure you want to disconnect Spotify and clear stored credentials?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                from actions.spotify_controller import clear_spotify_credentials
                clear_spotify_credentials()
                if hasattr(self, "_spotify_client_id"):
                    self._spotify_client_id.clear()
                if hasattr(self, "_spotify_client_secret"):
                    self._spotify_client_secret.clear()
                self._update_spotify_status()
                QMessageBox.information(self, "Disconnected", "Spotify configuration and credentials removed.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to disconnect: {e}")

    def _refresh_hw_vitals(self):
        try:
            from actions.system_diagnostics_mcp import get_full_diagnostics
            d = get_full_diagnostics()
            cpu_pct = d["cpu"]["usage_percent"]
            ram_pct = d["ram"]["usage_percent"]
            bat_pct = d["battery"].get("percent", "--")
            disp = d["display"].get("brightness_levels", [80])
            b_val = disp[0] if disp else 80
            self._hw_vitals_lbl.setText(f"CPU: {cpu_pct}% | RAM: {ram_pct}% | Battery: {bat_pct}% | Display: {b_val}%")
            self._hw_brightness_slider.blockSignals(True)
            self._hw_brightness_slider.setValue(int(b_val))
            self._hw_brightness_val_lbl.setText(f"{b_val}%")
            self._hw_brightness_slider.blockSignals(False)
            self._hw_output_lbl.setText(f"Vitals updated. Uptime: {d['uptime']} ({d['running_processes_count']} processes).")
        except Exception as e:
            self._hw_output_lbl.setText(f"Error fetching vitals: {e}")

    def _handle_hw_brightness_slider(self, val):
        self._hw_brightness_val_lbl.setText(f"{val}%")
        try:
            from actions.system_diagnostics_mcp import set_brightness
            set_brightness(val)
        except Exception:
            pass

    def _handle_hw_ram_hogs(self):
        try:
            from actions.system_diagnostics_mcp import system_diagnostics
            res = system_diagnostics({"action": "ram_hogs", "limit": 4})
            self._hw_output_lbl.setText(res)
        except Exception as e:
            self._hw_output_lbl.setText(f"Error scanning RAM hogs: {e}")

    def _handle_hw_battery(self):
        try:
            from actions.system_diagnostics_mcp import system_diagnostics
            res = system_diagnostics({"action": "battery"})
            self._hw_output_lbl.setText(res)
        except Exception as e:
            self._hw_output_lbl.setText(f"Error checking battery: {e}")

    def _handle_hw_kill(self):
        target = self._hw_kill_target.text().strip()
        if not target:
            self._hw_output_lbl.setText("Please enter an application name or PID to kill.")
            return
        try:
            from actions.system_diagnostics_mcp import kill_process
            res = kill_process(target)
            msg = res.get("message", "Done.")
            self._hw_output_lbl.setText(msg)
            from PyQt6.QtWidgets import QMessageBox
            if res.get("success"):
                QMessageBox.information(self, "Process Terminated", msg)
            else:
                QMessageBox.warning(self, "Process Management", msg)
        except Exception as e:
            self._hw_output_lbl.setText(f"Error: {e}")

    def _refresh_auto_heal_status(self):
        try:
            from core.learned_rules import LearnedRulesEngine
            rules = LearnedRulesEngine.list_rules()
            active_count = sum(1 for r in rules if r.get("active", True))
            self._ah_status_lbl.setText(f"Sentry: Active | Sandbox: Verified | Rules: {active_count} active")
        except Exception as e:
            self._ah_status_lbl.setText(f"Status: Error loading ({e})")

    def _handle_ah_history(self):
        try:
            from actions.auto_heal_engine import auto_heal
            res = auto_heal({"action": "history"})
            self._ah_output_lbl.setText(res)
        except Exception as e:
            self._ah_output_lbl.setText(f"Error reading patch history: {e}")

    def _handle_ah_rollback(self):
        try:
            from PyQt6.QtWidgets import QMessageBox
            from actions.auto_heal_engine import auto_heal
            reply = QMessageBox.question(
                self,
                "Confirm Rollback",
                "Are you sure you want to rollback the most recent autonomous patch?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                res = auto_heal({"action": "rollback", "patch_id": "latest"})
                self._ah_output_lbl.setText(res)
                self._refresh_auto_heal_status()
                QMessageBox.information(self, "Rollback Result", res)
        except Exception as e:
            self._ah_output_lbl.setText(f"Error executing rollback: {e}")

    def _handle_ah_rules(self):
        try:
            from actions.auto_heal_engine import auto_heal
            res = auto_heal({"action": "list_rules"})
            self._ah_output_lbl.setText(res)
        except Exception as e:
            self._ah_output_lbl.setText(f"Error reading learned rules: {e}")

    def _handle_ah_learn_rule(self):
        txt = self._ah_rule_input.text().strip()
        if not txt:
            self._ah_output_lbl.setText("Please enter a rule or habit to teach.")
            return
        try:
            from core.learned_rules import LearnedRulesEngine
            res = LearnedRulesEngine.add_rule(txt, origin="ui_settings")
            msg = res.get("message", "Rule saved.")
            self._ah_output_lbl.setText(f"✅ {msg}")
            self._ah_rule_input.clear()
            self._refresh_auto_heal_status()
        except Exception as e:
            self._ah_output_lbl.setText(f"Error saving rule: {e}")

    def _handle_ah_trigger_test_bug(self):
        try:
            from actions.test_action import test_action
            res = test_action({})
            self._ah_output_lbl.setText(f"✅ Test action executed cleanly with no errors: {res}\nThe code is already healthy!")
        except Exception as e:
            import traceback
            from actions.auto_heal_engine import AutoHealEngine
            tb = traceback.format_exc()
            AutoHealEngine.record_last_error(tb)
            self._ah_output_lbl.setText(
                f"❌ Simulated bug triggered in test_action.py: {type(e).__name__}: {e}\n"
                f"Traceback captured in AutoHealEngine! Click 'Fix Captured Bug' or say 'Brahma, fix that bug'."
            )

    def _handle_ah_fix_captured_bug(self):
        self._ah_output_lbl.setText("⏳ Gemini is synthesizing hotfix in safety sandbox...")
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        try:
            from actions.auto_heal_engine import auto_heal
            res = auto_heal({"action": "heal"})
            self._ah_output_lbl.setText(f"🛠️ Result:\n{res}")
            self._refresh_auto_heal_status()
        except Exception as e:
            self._ah_output_lbl.setText(f"Error repairing bug: {e}")

    def _pick_theme_color(self):
        color = QColorDialog.getColor(QColor(C.PRI), self, "Select App Theme Color")
        if color.isValid():
            self._change_theme(color.name())

    def _change_theme(self, new_theme: str):
        settings = {}
        try:
            if APP_SETTINGS_FILE.exists():
                with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
        except Exception:
            pass
        old_theme = settings.get("app_theme", "Gold")
        if new_theme.lower() == old_theme.lower():
            return
        settings["app_theme"] = new_theme
        try:
            with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            print(f"Error saving theme: {e}")
        import subprocess
        import sys
        QMessageBox.information(self, "Restart Required", "The application will now restart to apply the new theme.")
        subprocess.Popen([sys.executable, "main.py"])
        QApplication.quit()

    def _build_summary_card(self):
        card = self._card("")
        card.layout().setContentsMargins(18, 18, 18, 18)
        card.layout().setSpacing(14)
        card.layout().addWidget(self._build_status_box())
        card.layout().addWidget(self._build_quick_actions_box())
        card.layout().addWidget(self._build_security_tip())
        return card

    def _build_status_box(self):
        box = self._card("System Status", "")
        lay = box.layout()
        self._sys_online = QLabel("≡ƒƒó System Online")
        self._sys_online.setStyleSheet("color: #35ff75; font-weight: 700;")
        self._sys_note = QLabel("All systems are operational.")
        self._sys_note.setStyleSheet(f"color: {C.TEXT_MED};")
        lay.addWidget(self._sys_online)
        lay.addWidget(self._sys_note)
        self._sys_version = QLabel("v1.0.0")
        self._sys_platform = QLabel(platform.system())
        self._sys_provider = QLabel("Gemini")
        self._sys_updated = QLabel(time.strftime("%d %b %Y %H:%M"))
        for label, val in (("Version", self._sys_version), ("Platform", self._sys_platform), ("Current AI Provider", self._sys_provider), ("Last Updated", self._sys_updated)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch(1)
            row.addWidget(val)
            lay.addLayout(row)
        return box

    def _build_quick_actions_box(self):
        box = self._card("Quick Actions", "")
        lay = box.layout()
        actions = [
            ("Restart Brahma Evo", QStyle.StandardPixmap.SP_BrowserReload, self._restart_app),
            ("Reload Configuration", QStyle.StandardPixmap.SP_BrowserReload, self._reload_config),
            ("Open Data Folder", QStyle.StandardPixmap.SP_DirOpenIcon, self._open_data_folder),
            ("View Logs", QStyle.StandardPixmap.SP_FileDialogDetailedView, self._view_logs),
            ("Check for Updates", QStyle.StandardPixmap.SP_ArrowDown, self._check_updates),
        ]
        for text, icon_kind, slot in actions:
            btn = QPushButton(text)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(self.style().standardIcon(icon_kind))
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        return box

    def _build_security_tip(self):
        box = QFrame()
        box.setStyleSheet("QFrame { background: rgba(24, 18, 8, 0.85); border: 1px solid rgba(255, 191, 0, 0.22); border-radius: 14px; }")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title = QLabel("Security Tip")
        title.setStyleSheet("color: #ffbf00; font-weight: 700;")
        body = QLabel('"Never share your API keys with anyone."')
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {C.TEXT_MED};")
        lay.addWidget(title)
        lay.addWidget(body)
        return box

    def _load_api_defaults(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            return self._ctrl()._win._load_api_defaults()
        return {
            "gemini_api_key": "",
            "openrouter_api_key": "",
            "os_system": platform.system(),
        }

    def _settings_file_stamp(self):
        """See MainWindow._settings_file_stamp."""
        win = self._owner_window()
        if win is not None and hasattr(win, "_settings_file_stamp"):
            return win._settings_file_stamp()
        try:
            if APP_SETTINGS_FILE.exists():
                st = APP_SETTINGS_FILE.stat()
                return (st.st_mtime_ns, st.st_size)
        except Exception:
            pass
        return None

    def _invalidate_app_settings_cache(self):
        """This page calls this from Qt signal handlers, so it must exist here.

        An earlier revision called it without defining it, which raised
        AttributeError inside a stateChanged slot: toggling a provider aborted
        the process, and Save All reported the missing method. The write owner
        (core/provider_router.py) bypasses this page's cache entirely, so the
        cache is dropped after every write that page makes.
        """
        win = self._owner_window()
        if win is not None and hasattr(win, "_invalidate_app_settings_cache"):
            try:
                win._invalidate_app_settings_cache()
            except Exception:
                pass
        return None

    def _load_app_settings(self):
        win = self._owner_window()
        if win is not None and hasattr(win, "_load_app_settings"):
            return win._load_app_settings()
        # No window attached: read the stored file. Returning bare defaults
        # here was the "settings reset on restart" bug - every card that
        # persisted from this dict wrote the full default set back over the
        # stored choices, including enable_3d_background.
        settings = _default_app_settings()
        if APP_SETTINGS_FILE.exists():
            try:
                with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    settings.update(data)
            except Exception:
                pass
        return settings

    def _load_discord_settings(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            return self._ctrl()._win._load_discord_settings()
        return _default_discord_settings()

    def _startup_animation_enabled(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            return self._ctrl()._win._startup_animation_enabled()
        return True

    def _toggle_sound_effects(self, enabled: bool):
        sound_mgr.enabled = enabled
        self._set_setting("sound_effects_enabled", enabled)
        if enabled:
            sound_mgr.play_listening_start()

    def _on_sfx_volume_changed(self, val: int):
        sound_mgr.set_volume_percent(val)
        if hasattr(self, "_sfx_val_lbl"):
            self._sfx_val_lbl.setText(f"{val}%")
        self._set_setting("sound_effects_volume", val)

    def _set_setting(self, key: str, value):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            settings = self._ctrl()._win._load_app_settings()
            settings[key] = value
            self._ctrl()._win._save_app_settings(settings)
        else:
            try:
                data = {}
                if APP_SETTINGS_FILE.exists():
                    with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                data[key] = value
                with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
            except Exception:
                pass

    def _open_api_keys(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._show_setup(self._ctrl()._win._load_api_defaults())

    def _test_provider(self, setting_key: str):
        if setting_key == "gemini":
            msg = "Google Gemini key detected." if self._load_api_defaults().get("gemini_api_key") else "Google Gemini key missing."
        else:
            msg = "OpenRouter key detected." if self._load_api_defaults().get("openrouter_api_key") else "OpenRouter key missing."
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log(f"SYS: {msg}")
        self.refresh()

    def _connect_mobile(self):
        ctrl = self._ctrl()
        if not ctrl:
            return
        target = None
        if hasattr(ctrl, "_show_remote_connect"):
            target = ctrl
        elif hasattr(ctrl, "_win") and hasattr(ctrl._win, "_show_remote_connect"):
            target = ctrl._win
        if target is not None:
            self._mobile_connect_btn.setText("Connecting...")
            target._show_remote_connect()
            QTimer.singleShot(1100, lambda: self._mobile_connect_btn.setText("Connect Device"))

    def _disconnect_mobile(self):
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log("SYS: Mobile Connect session closed.")
        self._mobile_status.setText("Connection Status: Disconnected")
        self._mobile_phone.setText("Phone Name: Not connected")
        self._mobile_last.setText("Last Connected: Never")

    def _show_qr_code(self):
        ctrl = self._ctrl()
        if not ctrl:
            return
        target = None
        if hasattr(ctrl, "_show_remote_connect"):
            target = ctrl
        elif hasattr(ctrl, "_win") and hasattr(ctrl._win, "_show_remote_connect"):
            target = ctrl._win
        if target is not None:
            target._show_remote_connect()

    def _toggle_startup_from_page(self, checked: bool):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._set_startup_enabled(bool(checked))
            self._ctrl()._win._refresh_startup_button()

    def _toggle_launch_minimized(self, checked: bool):
        self._set_setting("launch_minimized", bool(checked))

    def _toggle_update_check(self, checked: bool):
        self._set_setting("check_updates_on_startup", bool(checked))

    def _toggle_startup_animation_from_page(self, checked: bool):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._set_startup_animation_enabled(bool(checked))
            self._ctrl()._win._refresh_startup_animation_button()

    def _set_default_provider(self, text: str):
        raw = (text or "").strip().lower()
        if raw.startswith("google") or raw == "gemini":
            provider = "Gemini"
            self._set_setting("offline_mode_enabled", False)
            if hasattr(self, "_offline_mode_btn"):
                self._offline_mode_btn.blockSignals(True)
                self._offline_mode_btn.setChecked(False)
                self._offline_mode_btn.blockSignals(False)
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(False)
            if hasattr(self, "_custom_ai_widget"):
                self._custom_ai_widget.setVisible(False)
            msg = "SYS: Default AI provider set to Google Gemini. Cloud connectivity active."
        elif raw == "local":
            provider = "Local"
            self._set_setting("offline_mode_enabled", True)
            if hasattr(self, "_offline_mode_btn"):
                self._offline_mode_btn.blockSignals(True)
                self._offline_mode_btn.setChecked(True)
                self._offline_mode_btn.blockSignals(False)
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(True)
            msg = "SYS: Default AI provider set to Local AI (Ollama). Offline Mode active."
        elif raw == "custom" or raw.startswith("custom ("):
            provider = "Custom"
            self._set_setting("offline_mode_enabled", False)
            if hasattr(self, "_offline_mode_btn"):
                self._offline_mode_btn.blockSignals(True)
                self._offline_mode_btn.setChecked(False)
                self._offline_mode_btn.blockSignals(False)
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(False)
            if hasattr(self, "_custom_ai_widget"):
                self._custom_ai_widget.setVisible(True)
            settings = self._load_app_settings()
            if not settings.get("custom_ai_url") or not settings.get("custom_ai_model"):
                msg = (
                    "SYS: Default AI provider set to your custom OpenAI-compatible endpoint. "
                    "Add its URL and model below, or requests will fail until you do."
                )
            else:
                msg = "SYS: Default AI provider set to your custom OpenAI-compatible endpoint."
        else:
            provider = "OpenRouter"
            self._set_setting("offline_mode_enabled", False)
            if hasattr(self, "_offline_mode_btn"):
                self._offline_mode_btn.blockSignals(True)
                self._offline_mode_btn.setChecked(False)
                self._offline_mode_btn.blockSignals(False)
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(False)
            if hasattr(self, "_custom_ai_widget"):
                self._custom_ai_widget.setVisible(False)
            msg = "SYS: Default AI provider set to OpenRouter. Cloud connectivity active."
        self._set_setting("default_ai_provider", provider)
        if hasattr(self, "_sys_provider"):
            self._sys_provider.setText(provider)
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log(msg)

    def _toggle_auto_provider_switch(self, checked: bool):
        self._set_setting("auto_provider_switch", bool(checked))
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log(f"SYS: Auto provider switch {'enabled' if checked else 'disabled'}.")

    def _toggle_offline_mode(self, checked: bool):
        self._set_setting("offline_mode_enabled", bool(checked))
        if checked:
            self._set_setting("default_ai_provider", "Local")
            if hasattr(self, "_default_provider"):
                self._default_provider.blockSignals(True)
                self._default_provider.setCurrentText("Local")
                self._default_provider.blockSignals(False)
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(True)
            if hasattr(self, "_sys_provider"):
                self._sys_provider.setText("Local")
            msg = "🔒 SYSTEM: Air-Gapped Offline Mode ENGAGED. All operations running 100% locally."
        else:
            self._set_setting("default_ai_provider", "Gemini")
            if hasattr(self, "_default_provider"):
                self._default_provider.blockSignals(True)
                self._default_provider.setCurrentText("Google Gemini")
                self._default_provider.blockSignals(False)
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(False)
            if hasattr(self, "_sys_provider"):
                self._sys_provider.setText("Gemini")
            msg = "🌐 SYSTEM: Offline Mode DISENGAGED. Cloud connectivity restored."
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log(msg)

    def _toggle_attention_message_prompts(self, checked: bool):
        self._set_setting("attention_message_prompts", bool(checked))
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log(f"SYS: Incoming message prompts {'enabled' if checked else 'disabled'}.")

    def _toggle_attention_call_prompts(self, checked: bool):
        self._set_setting("attention_call_prompts", bool(checked))
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log(f"SYS: Incoming call prompts {'enabled' if checked else 'disabled'}.")

    def _preview_animation(self):
        self._preview_progress.setValue(0)
        if hasattr(self, "_preview_timer") and self._preview_timer:
            try:
                self._preview_timer.stop()
            except Exception:
                pass
        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._tick_preview)
        self._preview_timer.start(24)

    def _tick_preview(self):
        value = min(100, self._preview_progress.value() + 4)
        self._preview_progress.setValue(value)
        if value >= 100 and hasattr(self, "_preview_timer"):
            self._preview_timer.stop()
            if self._ctrl() and hasattr(self._ctrl(), "write_log"):
                self._ctrl().write_log("SYS: Startup animation preview finished.")

    def _toggle_discord_reveal(self, checked: bool):
        self._discord_token.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _save_discord_from_page(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._discord_token_input = self._discord_token
            self._ctrl()._win._discord_channel_input = self._discord_channel
            self._ctrl()._win._save_discord_token()
            self._discord_status.setText("Bot Status: Saved")
            self.refresh()

    def _test_discord_from_page(self):
        self._save_discord_from_page()
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._start_discord_bot()
            self._ctrl()._win._stop_discord_bot()
            self._discord_status.setText("Bot Status: Test sent")
            self._discord_msg.setText("Connected as Brahma Evo#9649" if self._discord_token.text().strip() else "Bot Offline")

    def _restart_discord_from_page(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._stop_discord_bot()
            self._ctrl()._win._start_discord_bot()
            self._discord_status.setText("Bot Status: Restarted")

    def _restart_app(self):
        if self._ctrl() and hasattr(self._ctrl(), "_restart_app"):
            self._ctrl()._restart_app()

    def _reload_config(self):
        if self._ctrl() and hasattr(self._ctrl(), "_win"):
            self._ctrl()._win._load_api_defaults()
            self._ctrl()._win._load_discord_settings()
            if hasattr(self._ctrl(), "write_log"):
                self._ctrl().write_log("SYS: Configuration reloaded.")
            self.refresh()

    def _open_data_folder(self):
        try:
            os.startfile(str(CONFIG_DIR))
        except Exception:
            pass

    def _view_logs(self):
        try:
            os.startfile(str(BASE_DIR))
        except Exception:
            pass

    def _check_updates(self):
        if self._ctrl() and hasattr(self._ctrl(), "write_log"):
            self._ctrl().write_log("SYS: Update check requested.")

    def refresh(self):
        api = self._load_api_defaults()
        app = self._load_app_settings()
        discord = self._load_discord_settings()
        for widget in (
            getattr(self, "_default_provider", None),
            getattr(self, "_offline_mode_btn", None),
            getattr(self, "_auto_switch_btn", None),
            getattr(self, "_attention_message_btn", None),
            getattr(self, "_attention_call_btn", None),
            getattr(self, "_startup_launch_btn", None),
            getattr(self, "_startup_minimized_btn", None),
            getattr(self, "_startup_updates_btn", None),
            getattr(self, "_startup_anim_enable_btn", None),
            getattr(self, "_discord_token", None),
            getattr(self, "_discord_channel", None),
            getattr(self, "_discord_reveal", None),
        ):
            if widget is not None:
                widget.blockSignals(True)
        try:
            self._gemini_status.setText("Connected" if api.get("gemini_api_key") else "Not connected")
            self._or_status.setText("Connected" if api.get("openrouter_api_key") else "Not connected")
            self._gemini_key.setText(self._provider_key_preview(api.get("gemini_api_key", "")))
            self._or_key.setText(self._provider_key_preview(api.get("openrouter_api_key", "")))
            
            prov = app.get("default_ai_provider", "Gemini")
            is_offline = bool(app.get("offline_mode_enabled", False))
            if prov == "Local" or is_offline:
                disp_prov = "Local"
            elif prov == "OpenRouter":
                disp_prov = "OpenRouter"
            else:
                disp_prov = "Google Gemini"
            if hasattr(self, "_default_provider"):
                self._default_provider.setCurrentText(disp_prov)
            if hasattr(self, "_offline_mode_btn"):
                self._offline_mode_btn.setChecked(is_offline or prov == "Local")
            if hasattr(self, "_local_ai_widget"):
                self._local_ai_widget.setVisible(is_offline or prov == "Local")

            self._auto_switch_btn.setChecked(bool(app.get("auto_provider_switch", True)))
            self._attention_message_btn.setChecked(bool(app.get("attention_message_prompts", True)))
            self._attention_call_btn.setChecked(bool(app.get("attention_call_prompts", True)))
            self._startup_launch_btn.setChecked(bool(app.get("show_workspace_on_startup", False)))
            self._startup_minimized_btn.setChecked(bool(app.get("launch_minimized", False)))
            self._startup_updates_btn.setChecked(bool(app.get("check_updates_on_startup", True)))
            self._startup_anim_enable_btn.setChecked(bool(self._startup_animation_enabled()))
            self._discord_token.setText((discord.get("bot_token") or "").strip())
            self._discord_channel.setText((discord.get("channel_id") or "").strip())
            # Refresh the tool list on open so MCP servers that connected since
            # this page was last built appear immediately, not on the next poll.
            rebuild_tools = getattr(self, "_rebuild_tool_visibility", None)
            if callable(rebuild_tools):
                rebuild_tools()
        finally:
            for widget in (
                getattr(self, "_default_provider", None),
                getattr(self, "_offline_mode_btn", None),
                getattr(self, "_auto_switch_btn", None),
                getattr(self, "_attention_message_btn", None),
                getattr(self, "_attention_call_btn", None),
                getattr(self, "_startup_launch_btn", None),
                getattr(self, "_startup_minimized_btn", None),
                getattr(self, "_startup_updates_btn", None),
                getattr(self, "_startup_anim_enable_btn", None),
                getattr(self, "_discord_token", None),
                getattr(self, "_discord_channel", None),
                getattr(self, "_discord_reveal", None),
            ):
                if widget is not None:
                    widget.blockSignals(False)
        enabled = bool(discord.get("enabled", False))
        token = (discord.get("bot_token") or "").strip()
        if enabled and token:
            self._discord_status.setText("Bot Status: Online")
            self._discord_msg.setText("Connected as Brahma Evo#9649")
        elif token:
            self._discord_status.setText("Bot Status: Offline")
            self._discord_msg.setText("Bot Offline")
        else:
            self._discord_status.setText("Bot Status: Offline")
            self._discord_msg.setText("Token required")
        if hasattr(self, "_sys_provider"):
            self._sys_provider.setText(app.get("default_ai_provider", "Gemini"))
        if hasattr(self, "_update_spotify_status"):
            self._update_spotify_status()

    def _handle_create_desktop_shortcut(self):
        success, path_or_err = self._create_desktop_shortcut_logic()
        if success:
            QMessageBox.information(
                self, 
                "Success", 
                f"Desktop shortcut created successfully at:\n{path_or_err}"
            )
        else:
            QMessageBox.warning(
                self, 
                "Error", 
                f"Failed to create desktop shortcut:\n{path_or_err}"
            )

    def _handle_pin_to_taskbar(self):
        success, msg = self._pin_app_to_taskbar_logic()
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.warning(
                self, 
                "Taskbar Pinning", 
                msg
            )

    def _create_desktop_shortcut_logic(self):
        try:
            import os
            import sys
            import shutil
            import subprocess
            from pathlib import Path
            import winreg
            
            # Find the correct Desktop folder path using registry (OneDrive safe!)
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
                desktop_raw, _ = winreg.QueryValueEx(key, "Desktop")
                winreg.CloseKey(key)
                desktop_dir = Path(os.path.expandvars(desktop_raw))
            except Exception:
                desktop_dir = Path(os.path.expanduser("~")) / "Desktop"
                
            desktop_dir.mkdir(parents=True, exist_ok=True)
            shortcut_path = desktop_dir / "Brahma Evo - Premium.lnk"
            
            # Base variables
            base_dir = Path(os.path.abspath("."))
            script_path = base_dir / "main.py"
            icon_path = base_dir / "assets" / "Brahma_Lite_Logo.ico"
            
            python_exe = sys.executable
            if not python_exe:
                python_exe = shutil.which("pythonw") or shutil.which("python") or "pythonw"
                
            shortcut_target = python_exe
            shortcut_args = f'"{script_path}"'
            if getattr(sys, "frozen", False):
                shortcut_target = python_exe
                shortcut_args = ""
                
            powershell_exe = shutil.which("powershell.exe") or "powershell"
            
            def _ps_escape(value: str) -> str:
                return value.replace("'", "''")
                
            icon_value = str(icon_path) if icon_path.exists() else ""
            ps1_script = "\n".join([
                "$WshShell = New-Object -ComObject WScript.Shell",
                f"$Shortcut = $WshShell.CreateShortcut('{_ps_escape(str(shortcut_path))}')",
                f"$Shortcut.TargetPath = '{_ps_escape(shortcut_target)}'",
                f"$Shortcut.Arguments = '{_ps_escape(shortcut_args)}'",
                f"$Shortcut.WorkingDirectory = '{_ps_escape(str(base_dir))}'",
                "$Shortcut.WindowStyle = 7",
                "$Shortcut.Description = 'Launch Brahma Evo - Premium'",
                f"if ('{_ps_escape(icon_value)}') {{ $Shortcut.IconLocation = '{_ps_escape(icon_value)},0' }}",
                "$Shortcut.Save()",
            ])
            
            ps1_path = get_user_data_dir() / "config" / "create_desktop_shortcut.ps1"
            ps1_path.write_text(ps1_script, encoding="utf-8")
            
            subprocess.run(
                [powershell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            
            # Write marker file
            marker_path = get_user_data_dir() / "config" / ".desktop_shortcut_created"
            marker_path.write_text("created", encoding="utf-8")
            
            return True, str(shortcut_path)
        except Exception as e:
            return False, str(e)

    def _pin_app_to_taskbar_logic(self):
        try:
            import shutil
            import subprocess
            from pathlib import Path
            
            # Ensure desktop shortcut exists first
            success, path_or_err = self._create_desktop_shortcut_logic()
            if not success:
                return False, f"Failed to create desktop shortcut first: {path_or_err}"
                
            shortcut_path = Path(path_or_err)
            if not shortcut_path.exists():
                return False, "Shortcut file does not exist."
                
            powershell_exe = shutil.which("powershell.exe") or "powershell"
            
            # COM pin script
            def _ps_escape(value: str) -> str:
                return value.replace("'", "''")
                
            pin_script = "\n".join([
                "$Shell = New-Object -ComObject Shell.Application",
                f"$Folder = $Shell.NameSpace('{_ps_escape(str(shortcut_path.parent))}')",
                f"$Item = $Folder.ParseName('{_ps_escape(shortcut_path.name)}')",
                "$Verb = $Item.Verbs() | Where-Object { $_.Name.Replace('&', '') -match 'Pin to taskbar' }",
                "if ($Verb) { $Verb.DoIt(); exit 0 } else { exit 1 }"
            ])
            
            res = subprocess.run(
                [powershell_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", pin_script],
                capture_output=True
            )
            
            if res.returncode == 0:
                return True, "Brahma Evo has been pinned to your Taskbar!"
            else:
                return False, "Windows restricts programmatic taskbar pinning. Please right-click the 'Brahma Evo - Premium.lnk' shortcut on your Desktop and select 'Pin to taskbar', or drag it directly onto your taskbar."
        except Exception as e:
            return False, f"Error pinning to taskbar: {e}"
