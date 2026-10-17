"""The command bar, moved verbatim out of ui.py (P4-19).
Qt/stdlib/core imports are direct; _icon_pixmap is injected via bind()
after ui.py defines it."""
from __future__ import annotations

from PyQt6.QtCore import (QSize, Qt, pyqtSignal)
from PyQt6.QtGui import (QFont, QIcon)
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)
from core.ui_theme import (C)


def bind(*, _icon_pixmap):
    globals().update(_icon_pixmap=_icon_pixmap)

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
        frame.setStyleSheet("""
            QFrame#CommandBarFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(6, 6, 10, 252),
                    stop:0.3 rgba(12, 11, 16, 252),
                    stop:0.7 rgba(12, 11, 16, 252),
                    stop:1 rgba(6, 6, 10, 252));
                border: 1px solid rgba(0, 229, 255, 0.22);
                border-radius: 20px;
            }
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
        btn_style_ghost = """
            QPushButton {
                background: rgba(255, 255, 255, 0.03);
                color: rgba(255, 255, 255, 0.5);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 14px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.12);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.4);
            }
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
        dev.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.06);
                color: rgba(0, 229, 255, 0.7);
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 14px;
                font: 700 8px 'Segoe UI';
                letter-spacing: 0.5px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.15);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.5);
            }
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
        send.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.12);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 19px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.25);
                border: 1px solid #00e5ff;
            }
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
