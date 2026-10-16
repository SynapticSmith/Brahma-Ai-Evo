"""The first-run setup overlay, moved verbatim out of ui.py (P4-19).
Qt/stdlib/core imports are direct; APP_SETTINGS_FILE and _OS are injected
via bind() after ui.py defines them."""
from __future__ import annotations

import json

from PyQt6.QtCore import (QPointF, QTimer, Qt, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QPainter, QRadialGradient)
from PyQt6.QtWidgets import (QColorDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)
from core.identity import (identity)
from core.ui_theme import (C)


def bind(*, APP_SETTINGS_FILE, _OS):
    globals().update(APP_SETTINGS_FILE=APP_SETTINGS_FILE, _OS=_OS)

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
