"""Remote-key, daily-briefing, confirmation, and memory-inspector
overlays, moved verbatim out of ui.py (P4-19). Qt and stdlib imports are
direct; the C palette is imported from core.ui_theme."""
from __future__ import annotations

import time

from PyQt6.QtCore import (QTimer, Qt, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QPixmap, QSyntaxHighlighter, QTextCharFormat)
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)
from core.diff_preview import preview_detail
from core.ui_theme import (C)

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
            
        frame.setStyleSheet("""
            QFrame#RemoteOverlayMainFrame {
                background: rgba(10, 12, 18, 250);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 24px;
            }
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
        self._key_lbl.setStyleSheet("""
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
        self._frame.setStyleSheet("""
            QFrame#BriefingOverlayMainFrame {
                background: rgba(10, 12, 18, 250);
                border: 1px solid rgba(0, 229, 255, 0.4);
                border-radius: 20px;
            }
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


class DetailHighlighter(QSyntaxHighlighter):
    """Colour a plain-text approval detail so a diff is readable at a glance.

    The detail is text, not markup: a command plus rule, or a unified diff. This
    only sets character formats, so the underlying string the approver sees is
    unchanged and ``toPlainText()`` still returns exactly what was passed in.
    """

    _HEADINGS = ("rule:", "command:", "cwd:", "timeout:", "file:", "replace:", "tool:")

    def __init__(self, document):
        super().__init__(document)
        self._file = QTextCharFormat()
        self._file.setForeground(QColor(C.TEXT))
        self._file.setFontWeight(QFont.Weight.Bold)
        self._hunk = QTextCharFormat()
        self._hunk.setForeground(QColor(C.ACC))
        self._add = QTextCharFormat()
        self._add.setForeground(QColor("#4ade80"))
        self._del = QTextCharFormat()
        self._del.setForeground(QColor("#f87171"))
        self._dim = QTextCharFormat()
        self._dim.setForeground(QColor(C.TEXT_DIM))
        self._key = QTextCharFormat()
        self._key.setForeground(QColor(C.TEXT_MED))
        self._key.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text: str) -> None:
        if text.startswith(("+++", "---")) and not text.startswith("--- before"):
            self.setFormat(0, len(text), self._file)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self._hunk)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self._add)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self._del)
        elif text.startswith("…"):
            # Fold marker: deliberately dim, so it does not read as content.
            self.setFormat(0, len(text), self._dim)
        else:
            for heading in self._HEADINGS:
                if text.startswith(heading):
                    self.setFormat(0, len(heading), self._key)
                    break


class ConfirmationOverlay(QWidget):
    """Physical confirmation gate for irreversible actions (shutdown, restart, wifi toggles)."""
    answered = pyqtSignal(bool)
    always   = pyqtSignal()
    closed   = pyqtSignal()
    _OW = 440

    def __init__(
        self,
        title: str,
        detail: str,
        parent=None,
        *,
        allow_always: bool = False,
        remember_label: str = "",
    ):
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

        hdr = QLabel(
            "⚠  AGENT APPROVAL REQUIRED" if allow_always
            else "⚠  PHYSICAL CONFIRMATION REQUIRED"
        )
        hdr.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent; letter-spacing: 0.5px;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        self._detail_box = None
        self._detail_highlighter = None
        self._fold_button = None
        self._preview = preview_detail(detail or "")

        if detail:
            if "\n" in detail or len(detail) > 160:
                # A command plus a rule, or a diff, is not a one-line subtitle.
                # A read-only text box keeps the banner a sane height and
                # scrolls instead of pushing the buttons off screen.
                box = QPlainTextEdit(detail)
                box.setReadOnly(True)
                box.setFixedHeight(150)
                box.setFont(QFont("Consolas", 9))
                box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
                box.setStyleSheet(
                    f"QPlainTextEdit {{ color: {C.TEXT_MED};"
                    f" background: rgba(0, 0, 0, 0.35);"
                    f" border: 1px solid {C.BORDER}; border-radius: 6px;"
                    " padding: 6px; }"
                )
                self._detail_box = box
                self._detail_highlighter = DetailHighlighter(box.document())
                lay.addWidget(box)
                if self._preview.has_diff:
                    lay.addLayout(self._build_diff_row())
            else:
                dtl = QLabel(detail)
                dtl.setWordWrap(True)
                dtl.setFont(QFont("Segoe UI", 9))
                dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
                lay.addWidget(dtl)

        if allow_always and remember_label:
            hint = QLabel(
                f"ALWAYS ALLOW remembers {remember_label} until Brahma exits."
            )
            hint.setWordWrap(True)
            hint.setFont(QFont("Segoe UI", 8))
            hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(hint)

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

        if allow_always:
            keep = QPushButton("ALWAYS ALLOW")
            keep.setFixedHeight(36)
            keep.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            keep.setCursor(Qt.CursorShape.PointingHandCursor)
            keep.setToolTip("Approve this tool (or this exact command) for the rest of the session")
            keep.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(0, 229, 255, 0.06);
                    color: {C.TEXT_MED};
                    border: 1px solid {C.ACC};
                    border-radius: 6px;
                    padding: 0 12px;
                }}
                QPushButton:hover {{
                    background: rgba(0, 229, 255, 0.20);
                    color: {C.WHITE};
                }}
            """)
            keep.clicked.connect(self._on_always)
            row.addWidget(keep)

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

    def _build_diff_row(self) -> QHBoxLayout:
        """Summary plus a fold toggle, shown only when the detail has a diff."""
        row = QHBoxLayout()
        row.setSpacing(8)

        summary = QLabel(f"diff: {self._preview.summary}")
        summary.setFont(QFont("Segoe UI", 8))
        summary.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        row.addWidget(summary)
        row.addStretch(1)

        toggle = QPushButton("CHANGES ONLY")
        toggle.setFixedHeight(22)
        toggle.setFont(QFont("Segoe UI", 8))
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setToolTip("Hide unchanged context lines around each change")
        toggle.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255, 0.06);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 0 8px;
            }}
            QPushButton:hover {{
                color: {C.WHITE};
                border-color: {C.BORDER_B};
                background: rgba(0, 229, 255, 0.18);
            }}
        """)
        toggle.clicked.connect(self._toggle_fold)
        row.addWidget(toggle)
        self._fold_button = toggle
        return row

    def _toggle_fold(self):
        if self._detail_box is None or self._fold_button is None:
            return
        self._set_folded(not bool(self._detail_box.property("folded")))

    def _set_folded(self, folded: bool):
        """Swap the box between the full detail and its changes-only form."""
        if self._detail_box is None or self._fold_button is None:
            return
        self._detail_box.setProperty("folded", folded)
        self._detail_box.setPlainText(
            self._preview.folded if folded else self._preview.full
        )
        self._fold_button.setText("SHOW FULL DETAIL" if folded else "CHANGES ONLY")
        self._fold_button.setToolTip(
            "Show the whole detail again" if folded
            else "Hide unchanged context lines around each change"
        )

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

    def _on_always(self):
        if hasattr(self, "_timer") and self._timer.isActive():
            self._timer.stop()
        self.hide()
        self.always.emit()
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
