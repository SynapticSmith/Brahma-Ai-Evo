"""Chat/workspace widget cluster extracted from ui.py (P4-19).

Behavior-preserving move of the conversation feed, the task/artifact/event
cards and the inline chat workspace. Shared UI dependencies defined outside
this cluster (the colour palette ``C``, the ``_framed_logo`` helper and
``BASE_DIR``) are injected by :func:`bind`, which ``ui.py`` calls
immediately after importing this module. None of them are referenced at
class-definition time, so the injection order is safe.
"""

from __future__ import annotations

import html as html_lib
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
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
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from workspace_store import store as workspace_store


def bind(*, C, _framed_logo, BASE_DIR):
    """Inject the shared UI dependencies defined in ``ui.py``."""
    globals().update(C=C, _framed_logo=_framed_logo, BASE_DIR=BASE_DIR)


class MessageCard(QFrame):
    def __init__(self, role: str, name: str, text: str, stamp: str, parent=None):
        super().__init__(parent)
        self.setObjectName("MessageCard")
        accent_map = {
            "user": (C.BORDER_B, C.WHITE),
            "assistant": (C.PRI, C.PRI),
            "system": ("#4b8cff", "#4b8cff"),
            "file": ("#35c96d", "#35c96d"),
            "error": ("#ff8b3d", "#ff8b3d"),
        }
        border_col, left_col = accent_map.get(role, accent_map["system"])
        self.setStyleSheet(
            f"""
            QFrame#MessageCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(13, 15, 20, 246),
                    stop:1 rgba(5, 6, 9, 238));
                border: 1px solid {border_col};
                border-left: 3px solid {left_col};
                border-radius: 12px;
            }}
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)

        avatar = QLabel(name[:1].upper())
        avatar.setFixedSize(34, 34)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))

        palette = {
            "user": ("#0d0f14", C.WHITE, C.BORDER_B),
            "assistant": ("#12090a", C.RED, C.PRI),
            "system": ("#101525", "#7cb7ff", "#4b8cff"),
            "file": ("#0f1410", C.GREEN, "#35c96d"),
            "error": ("#1a0f10", "#ffb074", "#ff8b3d"),
        }
        bg, fg, border = palette.get(role, palette["system"])
        avatar.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 17px;"
        )

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(3)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")

        time_lbl = QLabel(stamp)
        time_lbl.setFont(QFont("Segoe UI", 7))
        time_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)

        top.addWidget(name_lbl)
        top.addStretch()
        top.addWidget(time_lbl)

        text_lbl = QLabel(text)
        text_lbl.setWordWrap(True)
        text_lbl.setFont(QFont("Segoe UI", 9))
        text_color = {
            "user": C.TEXT,
            "assistant": C.WHITE,
            "system": C.TEXT_MED,
            "file": C.GREEN,
            "error": C.RED,
        }.get(role, C.TEXT)
        text_lbl.setStyleSheet(f"color: {text_color}; background: transparent;")

        body.addLayout(top)
        body.addWidget(text_lbl)
        lay.addWidget(avatar)
        lay.addLayout(body)


class TaskCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskCard")
        self._active = False
        self._workspace_locked = False
        self.setStyleSheet(
            """
            QFrame#TaskCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 18, 24, 230),
                    stop:0.6 rgba(10, 12, 18, 210),
                    stop:1 rgba(5, 7, 11, 200));
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 16px;
            }
            QFrame#TaskCard:hover {
                border: 1px solid rgba(0, 229, 255, 0.28);
            }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self._title = QLabel("Task Workspace")
        self._title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {C.PRI}; background: transparent;")

        self._pct = QLabel("0%")
        self._pct.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._pct.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self._title)
        row.addStretch()
        row.addWidget(self._pct)
        lay.addLayout(row)

        self._command_lbl = QLabel("Command: waiting for input")
        self._command_lbl.setWordWrap(True)
        self._command_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._command_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._command_lbl)

        self._plan_lbl = QLabel("Plan: Brahma Evo will generate a task plan after you send a command.")
        self._plan_lbl.setWordWrap(True)
        self._plan_lbl.setFont(QFont("Segoe UI", 9))
        self._plan_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._plan_lbl)

        self._status_lbl = QLabel("Status: Idle")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._status_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._status_lbl)

        self._output_lbl = QLabel("Output: Ready to work.")
        self._output_lbl.setWordWrap(True)
        self._output_lbl.setFont(QFont("Segoe UI", 9))
        self._output_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(self._output_lbl)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setStyleSheet(
            f"""
            QProgressBar {{
                background: rgba(255,255,255,0.06);
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {C.PRI};
                border-radius: 3px;
            }}
            """
        )
        lay.addWidget(self._bar)

        self._foot = QLabel("Working on it...")
        self._foot.setFont(QFont("Segoe UI", 9))
        self._foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(self._foot)

    def set_task(self, title: str, desc: str, percent: int):
        if self._workspace_locked:
            return
        if self._active:
            self.update_workspace(title=title, status=desc, percent=percent)
            return
        self._title.setText(title)
        self._status_lbl.setText(desc)
        self._output_lbl.setText(desc)
        self._plan_lbl.setText("Plan: Brahma Evo will generate a task plan after you send a command.")
        self._command_lbl.setText("Command: waiting for input")
        self._pct.setText(f"{percent}%")
        self._bar.setValue(max(0, min(100, percent)))

    def _format_plan(self, plan: list[str] | str | None) -> str:
        if not plan:
            return "Plan: • Understand the request\n       • Execute the right tools\n       • Return the result"
        if isinstance(plan, str):
            text = plan.strip()
            return f"Plan: {text}" if text.lower().startswith("plan:") else f"Plan: {text}"
        items = [str(item).strip() for item in plan if str(item).strip()]
        if not items:
            return "Plan: • Understand the request\n       • Execute the right tools\n       • Return the result"
        return "Plan:\n" + "\n".join(f"• {item}" for item in items)

    def start_workspace(self, command: str, plan: list[str] | str | None = None, source: str = "local"):
        self._active = True
        self._workspace_locked = False
        self._title.setText("Task Workspace")
        self._command_lbl.setText(f"Command: {command or 'waiting for input'}")
        self._plan_lbl.setText(self._format_plan(plan))
        self._status_lbl.setText("Status: Planning task...")
        self._output_lbl.setText("Output: Waiting for execution.")
        self._pct.setText("8%")
        self._bar.setValue(8)
        self._foot.setText(f"Source: {source}")
        # self.show() intentionally removed to prevent flashing on quick/normal chats

    def update_workspace(self, *, title: str | None = None, command: str | None = None, plan: list[str] | str | None = None,
                         status: str | None = None, output: str | None = None, percent: int | None = None,
                         footer: str | None = None):
        if title:
            self._title.setText(title)
        if command:
            self._command_lbl.setText(f"Command: {command}")
        if plan is not None:
            self._plan_lbl.setText(self._format_plan(plan))
        if status:
            self._status_lbl.setText(f"Status: {status}" if not status.lower().startswith("status:") else status)
        if output:
            self._output_lbl.setText(f"Output: {output}" if not output.lower().startswith("output:") else output)
        if percent is not None:
            pct = max(0, min(100, int(percent)))
            self._pct.setText(f"{pct}%")
            self._bar.setValue(pct)
        self._foot.setText(footer)
        self._active = True
        self._workspace_locked = False
        if percent is not None and int(percent) > 10:
            self.show()

    def finish_workspace(self, result: str, status: str = "Task completed.", percent: int = 100):
        self._active = False
        self._workspace_locked = True
        self._title.setText("Task Complete")
        self._status_lbl.setText(f"Status: {status}")
        self._output_lbl.setText(f"Output: {result or 'Done.'}")
        self._pct.setText(f"{max(0, min(100, percent))}%")
        self._bar.setValue(max(0, min(100, percent)))
        self._foot.setText("Resetting workspace shortly...")
        self.show()
        QTimer.singleShot(5000, self.clear_workspace)

    def clear_workspace(self):
        self._active = False
        self._workspace_locked = False
        self._title.setText("Ready")
        self._command_lbl.setText("Command: waiting for input")
        self._plan_lbl.setText("Plan: Brahma Evo will generate a task plan after you send a command.")
        self._status_lbl.setText("Status: Idle")
        self._output_lbl.setText("Output: Ready to work.")
        self._pct.setText("0%")
        self._bar.setValue(0)
        self._foot.setText("Working on it...")
        self.hide()


def _fmt_time_stamp(value: int | float | None = None) -> str:
    try:
        from datetime import datetime
        if value is None:
            dt = datetime.now()
        else:
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000.0
            dt = datetime.fromtimestamp(stamp)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _markdown_to_html(text: str, role: str = "assistant") -> str:
    safe = html_lib.escape(text or "")
    safe = safe.replace("\r\n", "\n").replace("\r", "\n")

    def _code_block(match):
        code = html_lib.escape(match.group(1).rstrip("\n"))
        return (
            '<pre style="margin:10px 0; padding:10px 12px; border-radius:10px; '
            'background:rgba(0,0,0,0.35); color:#f4f6f8; border:1px solid rgba(255,255,255,0.10);">'
            f'<code>{code}</code></pre>'
        )

    safe = re.sub(r"```(?:[\w+-]+\n)?(.*?)```", _code_block, safe, flags=re.S)
    safe = re.sub(
        r"`([^`]+)`",
        r'<code style="padding:1px 5px; border-radius:5px; background:rgba(255,255,255,0.08); color:#fff;">\1</code>',
        safe,
    )
    safe = re.sub(r"(?m)^### (.+)$", r'<h3 style="margin:10px 0 6px 0; font-size:13px;">\1</h3>', safe)
    safe = re.sub(r"(?m)^## (.+)$", r'<h2 style="margin:10px 0 8px 0; font-size:15px;">\1</h2>', safe)
    safe = re.sub(r"(?m)^# (.+)$", r'<h1 style="margin:10px 0 8px 0; font-size:17px;">\1</h1>', safe)
    safe = safe.replace("\n", "<br>")
    accent = C.WHITE if role == "assistant" else C.TEXT
    return (
        f'<div style="color:{accent}; font-size:12px; line-height:1.45; white-space:normal;">'
        f"{safe}"
        "</div>"
    )


class AttachmentCard(QFrame):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("AttachmentCard")
        self.setStyleSheet("""
            QFrame#AttachmentCard {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(0, 229, 255,0.26);
                border-radius: 10px;
            }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        icon = QLabel("⎙")
        icon.setFixedSize(26, 26)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("color: #00e5ff; background: rgba(255,255,255,0.03); border-radius: 13px; font-size: 14px;")
        lay.addWidget(icon)
        txt = QVBoxLayout()
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("color: #ffffff; background: transparent; font: 600 9pt 'Segoe UI';")
        s = QLabel(subtitle)
        s.setStyleSheet("color: rgba(255,255,255,0.62); background: transparent; font: 8pt 'Segoe UI';")
        txt.addWidget(t)
        txt.addWidget(s)
        lay.addLayout(txt, 1)


class EventCard(QFrame):
    def __init__(self, title: str, detail: str, stamp: str, icon: str = "●", accent: str = "#00e5ff", parent=None):
        super().__init__(parent)
        self.setObjectName("EventCard")
        self.setStyleSheet(
            """
            QFrame#EventCard {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(0, 229, 255,0.15);
                border-radius: 12px;
            }
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 12, 6)
        lay.setSpacing(8)

        icon_lbl = QLabel(icon[:1])
        icon_lbl.setFixedSize(24, 24)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"background: transparent; color: {accent};")
        lay.addWidget(icon_lbl)

        clean_detail = (detail or "").strip()
        if len(clean_detail) > 160:
            clean_detail = clean_detail[:157] + "..."
        full_text = f"{title}: {clean_detail}" if clean_detail and clean_detail != title else title
        title_lbl = QLabel(full_text)
        title_lbl.setFont(QFont("Segoe UI", 9))
        title_lbl.setWordWrap(True)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title_lbl.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent;")
        lay.addWidget(title_lbl, 1)
        
        stamp_lbl = QLabel(stamp)
        stamp_lbl.setFont(QFont("Segoe UI", 7))
        stamp_lbl.setStyleSheet("color: rgba(255,255,255,0.4); background: transparent;")
        lay.addWidget(stamp_lbl)


class ArtifactCard(QFrame):
    def __init__(self, title: str, file_type: str = "File", status: str = "Generated", path: str = "", parent=None):
        super().__init__(parent)
        self._path = path.strip()
        self.setObjectName("ArtifactCard")
        self.setStyleSheet(
            """
            QFrame#ArtifactCard {
                background: rgba(11, 12, 16, 230);
                border: 1px solid rgba(0, 229, 255,0.22);
                border-radius: 12px;
            }
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255,0.08);
                border: 1px solid rgba(0, 229, 255,0.35);
            }
            QPushButton:disabled {
                color: rgba(255,255,255,0.35);
            }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        badge = QLabel("↗")
        badge.setFixedSize(30, 30)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        badge.setStyleSheet("background: rgba(0, 229, 255,0.08); color: #00e5ff; border: 1px solid rgba(0, 229, 255,0.24); border-radius: 15px;")
        head.addWidget(badge)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(2)
        name_lbl = QLabel(title or "Generated file")
        name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name_lbl.setStyleSheet("color: #ffffff; background: transparent;")
        type_lbl = QLabel(f"{file_type} • {status}")
        type_lbl.setFont(QFont("Segoe UI", 8))
        type_lbl.setStyleSheet("color: rgba(255,255,255,0.62); background: transparent;")
        meta.addWidget(name_lbl)
        meta.addWidget(type_lbl)
        head.addLayout(meta, 1)
        lay.addLayout(head)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._open_btn = QPushButton("Open")
        self._reveal_btn = QPushButton("Reveal Folder")
        self._open_btn.clicked.connect(self._open_file)
        self._reveal_btn.clicked.connect(self._reveal_file)
        if not self._path or not Path(self._path).exists():
            self._open_btn.setEnabled(False)
            self._reveal_btn.setEnabled(False)
        btn_row.addWidget(self._open_btn)
        btn_row.addWidget(self._reveal_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

    def _open_file(self):
        if not self._path or not Path(self._path).exists():
            return
        try:
            os.startfile(self._path)
        except Exception:
            pass

    def _reveal_file(self):
        if not self._path or not Path(self._path).exists():
            return
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["explorer", "/select,", self._path])
            else:
                os.startfile(str(Path(self._path).parent))
        except Exception:
            pass


class ChatBubble(QFrame):
    def __init__(self, role: str, name: str, text: str, stamp: str, attachments: list[dict] | None = None, parent=None, animate: bool = False):
        super().__init__(parent)
        self._role = role
        self._full_text = text or ""
        self._typing_index = 0
        self._typing_timer: QTimer | None = None
        self.setObjectName("ChatBubble")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setMinimumHeight(1)
        max_w = 340
        if parent and hasattr(parent, "viewport"):
            max_w = max(200, int(parent.viewport().width() * 0.84))
        self.setMaximumWidth(max_w)
        if role == "user":
            self.setMinimumWidth(120)

        bg_style = (
            "background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(20, 16, 12, 0.45), stop:1 rgba(10, 8, 5, 0.60)); border: 1px solid rgba(0, 229, 255, 0.25); border-radius: 16px;"
            if role != "user"
            else "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(0, 229, 255, 0.22), stop:1 rgba(210, 140, 0, 0.32)); border: 1px solid rgba(0, 229, 255, 0.55); border-radius: 16px;"
        )
        self.setStyleSheet(f"QFrame#ChatBubble {{ {bg_style} }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        if role == "assistant":
            avatar = _framed_logo(24, 24, bg="rgba(12,14,20,245)", border="rgba(0, 229, 255,0.50)", radius=12, inset=4)
            head.addWidget(avatar)
            name_lbl = QLabel(name or "Brahma Evo")
            name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            name_lbl.setStyleSheet("color: #ffffff; background: transparent;")
            head.addWidget(name_lbl)
        elif role != "user":
            name_lbl = QLabel(name)
            name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            name_lbl.setStyleSheet("color: #ffffff; background: transparent;")
            head.addWidget(name_lbl)
        elif role == "user":
            user_icon = QLabel("👤")
            user_icon.setFont(QFont("Segoe UI", 9))
            user_icon.setStyleSheet("color: #00e5ff; background: transparent;")
            head.addWidget(user_icon)
            name_lbl = QLabel(name or "You")
            name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            name_lbl.setStyleSheet("color: #ffffff; background: transparent;")
            head.addWidget(name_lbl)

        head.addStretch()

        time_lbl = QLabel(stamp)
        time_lbl.setFont(QFont("Segoe UI", 7))
        time_lbl.setStyleSheet("color: rgba(255,255,255,0.45); background: transparent;")
        head.addWidget(time_lbl)

        outer.addLayout(head)

        self._browser = QLabel()
        self._browser.setTextFormat(Qt.TextFormat.RichText)
        self._browser.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._browser.setOpenExternalLinks(True)
        self._browser.setWordWrap(True)
        self._browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._browser.setStyleSheet("QLabel { background: transparent; border: none; color: #f4f6f8; padding: 0; margin: 0; }")
        self._render_text(text or "")

        outer.addWidget(self._browser)

        if attachments:
            for attachment in attachments:
                title = str(attachment.get("name") or attachment.get("title") or attachment.get("path") or "Attachment")
                subtitle = str(attachment.get("path") or attachment.get("description") or "")
                outer.addWidget(ArtifactCard(title, file_type=Path(title).suffix.lstrip(".").upper() or "File", status="Attached", path=subtitle or title))

        if animate and role == "assistant":
            self._start_typing_animation()

    def _render_text(self, text: str, final: bool = True):
        self._browser.setText(_markdown_to_html(text or "", self._role))

    def _start_typing_animation(self):
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(14)
        self._typing_timer.timeout.connect(self._tick_typing)
        self._typing_timer.start()

    def _tick_typing(self):
        self._typing_index = min(len(self._full_text), self._typing_index + 3)
        snippet = self._full_text[:self._typing_index]
        self._render_text(snippet, final=False)
        if self._typing_index >= len(self._full_text):
            try:
                self._typing_timer.stop()
            except Exception:
                pass
            self._render_text(self._full_text, final=True)


class HistoryConversationItem(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, conversation_id: str, title: str, stamp: str, pinned: bool = False, parent=None):
        super().__init__(parent)
        self._conversation_id = conversation_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("HistoryConversationItem")
        self.setStyleSheet(
            """
            QFrame#HistoryConversationItem {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(0, 229, 255,0.18);
                border-radius: 10px;
            }
            QFrame#HistoryConversationItem:hover {
                background: rgba(0, 229, 255,0.07);
                border: 1px solid rgba(0, 229, 255,0.32);
            }
            """
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        icon = QLabel("B")
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("background: rgba(0, 229, 255,0.10); color: #00e5ff; border: 1px solid rgba(0, 229, 255,0.28); border-radius: 15px; font: 700 11pt 'Segoe UI';")
        lay.addWidget(icon)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        title_lbl = QLabel(title or "Conversation")
        title_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #ffffff; background: transparent;")
        row.addWidget(title_lbl)
        if pinned:
            pin = QLabel("PIN")
            pin.setStyleSheet("color: #37ff5f; background: transparent; font: 700 7pt 'Courier New';")
            row.addWidget(pin)
        row.addStretch()
        stamp_lbl = QLabel(stamp)
        stamp_lbl.setFont(QFont("Segoe UI", 7))
        stamp_lbl.setStyleSheet("color: rgba(255,255,255,0.50); background: transparent;")
        row.addWidget(stamp_lbl)
        meta.addLayout(row)
        lay.addLayout(meta, 1)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._conversation_id)


class ConversationFeed(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                border: none;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 229, 255,0.45);
                border-radius: 4px;
                min-height: 24px;
            }
            """
        )
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(8)
        self._layout.addStretch(1) # Stretch at bottom pushes bubbles tightly to the top
        self.setWidget(self._content)
        self._empty_widget: QWidget | None = None
        self._message_count = 0

    def _ensure_empty_widget(self):
        if self._empty_widget is not None:
            return self._empty_widget
        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(0, 229, 255,0.16);
                border-radius: 14px;
            }
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 8px 12px;
                text-align: left;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255,0.08);
                border: 1px solid rgba(0, 229, 255,0.28);
            }
            """
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        title = QLabel("Try asking Brahma Evo")
        title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff; background: transparent;")
        subtitle = QLabel("Create a presentation, analyze a screen, build a website, organize files, or run browser automation.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: rgba(255,255,255,0.64); background: transparent;")
        lay.addWidget(title)
        lay.addWidget(subtitle)
        grid = QGridLayout()
        grid.setSpacing(8)
        self._empty_widget_buttons = []
        for idx, suggestion in enumerate([
            "Create Presentation",
            "Analyze Screen",
            "Build Website",
            "Organize Downloads",
            "Browser Automation",
        ]):
            btn = QPushButton(suggestion)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, s=suggestion: self._emit_empty_suggestion(s))
            self._empty_widget_buttons.append(btn)
            grid.addWidget(btn, idx // 2, idx % 2)
        lay.addLayout(grid)
        self._empty_widget = frame
        return frame

    def _emit_empty_suggestion(self, text: str):
        root = self.parentWidget()
        while root is not None and not hasattr(root, "command_submitted"):
            root = root.parentWidget()
        if root is not None and hasattr(root, "command_submitted"):
            root.command_submitted.emit(text)

    def clear_messages(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._empty_widget:
                widget.deleteLater()
        if self._empty_widget is not None:
            self._empty_widget.hide()
        self._layout.addStretch(1)
        self._message_count = 0

    def add_message(self, role: str, name: str, text: str, stamp: str, attachments: list[dict] | None = None, animate: bool = False, event_type: str | None = None):
        vw = self.viewport().width()
        if hasattr(self, "_content") and self._content is not None and vw > 20:
            self._content.setFixedWidth(vw)
        if self._layout.count() and self._layout.itemAt(self._layout.count() - 1).spacerItem() is not None:
            self._layout.takeAt(self._layout.count() - 1)
        if role == "system":
            bubble = self._build_event_card(text, stamp, event_type=event_type)
            self._layout.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        elif role == "file":
            bubble = self._build_artifact_card(text, attachments=attachments, stamp=stamp)
            self._layout.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        elif role == "user":
            bubble = ChatBubble(role, name, text, stamp, attachments=attachments, parent=self, animate=animate)
            self._layout.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        else:
            bubble = ChatBubble(role, name, text, stamp, attachments=attachments, parent=self, animate=animate)
            self._layout.addWidget(bubble, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        self._layout.addStretch(1)
        self._message_count += 1
        self._sync_empty_state()
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _build_event_card(self, text: str, stamp: str, event_type: str | None = None) -> QWidget:
        low = (event_type or text or "").lower()
        title = "System Event"
        icon = "●"
        accent = "#00e5ff"
        if "discord" in low and "connected" in low:
            title, icon, accent = "Discord Connected", "◉", "#5865F2"
        elif "presentation" in low:
            title, icon, accent = "Presentation Generated", "▣", "#ff7a45"
        elif "website" in low:
            title, icon, accent = "Website Created", "⌂", "#37ff5f"
        elif "spreadsheet" in low:
            title, icon, accent = "Spreadsheet Generated", "▦", "#6fd6ff"
        elif "browser" in low:
            title, icon, accent = "Browser Automation Completed", "↗", "#ffbf00"
        elif "organ" in low and "file" in low:
            title, icon, accent = "File Organization Completed", "🗂", "#37ff5f"
        elif "screen" in low and "analysis" in low:
            title, icon, accent = "Screen Analysis Completed", "◫", "#6fd6ff"
        return EventCard(title, text, stamp, icon=icon, accent=accent, parent=self)

    def _build_artifact_card(self, text: str, attachments: list[dict] | None = None, stamp: str = "") -> QWidget:
        attachment = (attachments or [{}])[0] if attachments else {}
        path = str(attachment.get("path") or attachment.get("file") or attachment.get("name") or "").strip()
        title = str(attachment.get("name") or attachment.get("title") or Path(path).name or "Generated File").strip()
        suffix = Path(path or title).suffix.lstrip(".").upper() or (str(attachment.get("type") or "FILE")).upper()
        status = "Ready"
        return ArtifactCard(title, file_type=suffix, status=status, path=path or title, parent=self)

    def scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def load_messages(self, messages: list[dict[str, Any]], max_display: int = 40):
        self.clear_messages()
        # Cap batch to recent messages to prevent UI thread freeze with large histories
        display_messages = messages[-max_display:] if len(messages) > max_display else messages
        for msg in display_messages:
            role = (msg.get("role") or "assistant").strip().lower()
            content = msg.get("content") or ""
            stamp = _fmt_time_stamp(msg.get("timestamp"))
            attachments = msg.get("attachments") or []
            name = {
                "user": "You",
                "assistant": "Brahma Evo",
                "system": "System",
                "file": "Files",
            }.get(role, "Brahma Evo")
            self.add_message(role, name, content, stamp, attachments=attachments, animate=False)
        self._sync_empty_state()
        QTimer.singleShot(0, self.scroll_to_bottom)

    def _sync_empty_state(self):
        if self._empty_widget is None:
            self._ensure_empty_widget()
        if self._message_count <= 0:
            if self._layout.indexOf(self._empty_widget) == -1:
                self._layout.insertWidget(0, self._empty_widget)
            self._empty_widget.show()
        else:
            if self._layout.indexOf(self._empty_widget) != -1:
                self._layout.removeWidget(self._empty_widget)
            self._empty_widget.hide()

    def has_messages(self) -> bool:
        return self._message_count > 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        vw = self.viewport().width()
        if hasattr(self, "_content") and self._content is not None and vw > 20:
            self._content.setFixedWidth(vw)
            for i in range(self._layout.count()):
                w = self._layout.itemAt(i).widget()
                if w and isinstance(w, ChatBubble):
                    max_w = max(180, int(vw * 0.84))
                    w.setMaximumWidth(max_w)
                elif w and hasattr(w, "_fit_to_content"):
                    w._fit_to_content()
        QTimer.singleShot(0, self.scroll_to_bottom)


class TaskDock(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskDock")
        self._collapsed = False
        self._expanded_w = 392
        self._collapsed_w = 44
        self.setFixedWidth(self._expanded_w)
        self.setStyleSheet(
            """
            QFrame#TaskDock {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(7, 8, 12, 250),
                    stop:1 rgba(3, 4, 6, 245));
                border-left: 1px solid rgba(0, 229, 255, 0.55);
            }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 10, 8, 10)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel("TASK WORKSPACE")
        self._title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self._title.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        header.addWidget(self._title)
        header.addStretch()

        self._toggle_btn = QPushButton(">")
        self._toggle_btn.setFixedSize(34, 34)
        self._toggle_btn.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(12,14,18,245); color: {C.WHITE}; border: 1px solid {C.BORDER_B}; border-radius: 8px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.PRI}; }}"
        )
        self._toggle_btn.clicked.connect(self.toggle_collapsed)
        header.addWidget(self._toggle_btn)
        root.addLayout(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.PRI_GHO}; margin: 2px 0;")
        root.addWidget(sep)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self._content)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._task_card = TaskCard()
        lay.addWidget(self._task_card)

        self._mini_hint = QLabel("TASK")
        self._mini_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mini_hint.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._mini_hint.setStyleSheet(
            f"color: {C.PRI}; background: rgba(12,14,18,245); border: 1px solid {C.BORDER_B}; border-radius: 8px; padding: 10px 4px;"
        )
        self._mini_hint.setVisible(False)
        lay.addWidget(self._mini_hint)

        root.addWidget(self._content, stretch=1)
        self._apply_state()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._apply_state()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _apply_state(self):
        self._content.setVisible(not self._collapsed)
        self._mini_hint.setVisible(self._collapsed)
        self._toggle_btn.setText(">" if self._collapsed else "<")
        self._toggle_btn.setToolTip("Open task workspace" if self._collapsed else "Collapse task workspace")
        self._title.setVisible(not self._collapsed)
        self.setFixedWidth(self._collapsed_w if self._collapsed else self._expanded_w)

    def start_workspace(self, command: str, plan: list[str] | str | None = None, source: str = "local"):
        self._task_card.start_workspace(command, plan, source)
        if self._collapsed:
            return

    def update_workspace(self, **kwargs):
        self._task_card.update_workspace(**kwargs)

    def finish_workspace(self, result: str, status: str = "Task completed.", percent: int = 100):
        self._task_card.finish_workspace(result, status, percent)

    def clear_workspace(self):
        self._task_card.clear_workspace()


class WorkspaceSidebar(QWidget):
    command_submitted = pyqtSignal(str)
    close_requested = pyqtSignal()
    attach_requested = pyqtSignal()
    mic_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._collapsed = True
        self._expanded_w = 468
        self._target_h = 860
        self._active_conversation_id: str | None = None
        self._store = workspace_store()
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._panel = QFrame(self)
        self._panel.setObjectName("WorkspaceSidebarPanel")
        self._panel.setStyleSheet(
            """
            QFrame#WorkspaceSidebarPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(16, 18, 24, 230),
                stop:0.55 rgba(10, 12, 18, 210),
                stop:1 rgba(5, 7, 11, 200));
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 22px;
            }
            """
        )
        try:
            shadow = QGraphicsDropShadowEffect(self._panel)
            shadow.setBlurRadius(35)
            shadow.setColor(QColor(0, 0, 0, 72))
            shadow.setOffset(0, 12)
            self._panel.setGraphicsEffect(shadow)
        except Exception:
            pass
        root = QHBoxLayout(self._panel)
        root.setContentsMargins(14, 14, 12, 14)
        root.setSpacing(10)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        content = QVBoxLayout(self._content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._title = QLabel("BRAHMA EVO WORKSPACE")
        self._title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: 1px;")
        header.addWidget(self._title)
        header.addStretch()
        self._close_btn = QPushButton("✕")
        self._close_btn.setToolTip("Close Workspace")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(30, 30)
        self._close_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._close_btn.setStyleSheet(
            """
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 15px;
                padding: 0;
            }
            QPushButton:hover {
                background: rgba(255, 82, 82, 0.25);
                color: #FF5252;
                border: 1px solid rgba(255, 82, 82, 0.50);
            }
            """
        )
        self._close_btn.clicked.connect(self.close_requested.emit)
        header.addWidget(self._close_btn)
        content.addLayout(header)

        self._tab_row = QHBoxLayout()
        self._tab_row.setSpacing(8)
        self._chat_tab_btn = self._make_tab_button("CHAT", True)
        self._history_tab_btn = self._make_tab_button("HISTORY", False)
        self._chat_tab_btn.clicked.connect(lambda: self._set_tab(0))
        self._history_tab_btn.clicked.connect(lambda: self._set_tab(1))
        self._tab_row.addWidget(self._chat_tab_btn)
        self._tab_row.addWidget(self._history_tab_btn)
        self._tab_row.addStretch(1)
        content.addLayout(self._tab_row)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_chat_tab())
        self._stack.addWidget(self._build_history_tab())
        content.addWidget(self._stack, 1)

        root.addWidget(self._content, stretch=1)

        self._panel.hide()
        self.hide()
        self._set_tab(0)
        self._ensure_active_conversation()
        self._load_active_conversation()
        self._refresh_history()
        self._apply_state()

    def _make_tab_button(self, text: str, active: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(34)
        btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setStyleSheet(self._tab_style(active))
        return btn

    def _tab_style(self, active: bool) -> str:
        if active:
            return """
                QPushButton {
                    background: rgba(0, 229, 255,0.16);
                    color: #FFFFFF;
                    border: 1px solid rgba(0, 229, 255,180);
                    border-radius: 10px;
                    padding: 0 14px;
                }
            """
        return """
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.82);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255,0.08);
                border: 1px solid rgba(0, 229, 255,120);
            }
        """

    def _set_tab(self, index: int):
        index = 0 if index == 0 else 1
        self._stack.setCurrentIndex(index)
        self._chat_tab_btn.setStyleSheet(self._tab_style(index == 0))
        self._history_tab_btn.setStyleSheet(self._tab_style(index == 1))
        self._chat_tab_btn.setChecked(index == 0)
        self._history_tab_btn.setChecked(index == 1)
        if index == 1:
            self._history_search.setFocus(Qt.FocusReason.TabFocusReason)

    def _build_chat_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._task_card = TaskCard()
        lay.addWidget(self._task_card)

        self._memory_frame = QFrame()
        self._memory_frame.setVisible(False)
        self._memory_frame.setStyleSheet(
            """
            QFrame {
                background: rgba(0, 229, 255,0.05);
                border: 1px solid rgba(0, 229, 255,0.24);
                border-radius: 12px;
            }
            """
        )
        mem_lay = QVBoxLayout(self._memory_frame)
        mem_lay.setContentsMargins(12, 10, 12, 10)
        mem_lay.setSpacing(6)
        mem_title = QLabel("Using Memory:")
        mem_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        mem_title.setStyleSheet("color: #FFFFFF; background: transparent;")
        self._memory_items = QLabel("")
        self._memory_items.setWordWrap(True)
        self._memory_items.setStyleSheet("color: rgba(255,255,255,0.75); background: transparent;")
        mem_lay.addWidget(mem_title)
        mem_lay.addWidget(self._memory_items)
        lay.addWidget(self._memory_frame)

        self._feed = ConversationFeed()
        lay.addWidget(self._feed, 1)

        # Sleek modern chat input container for Workspace
        input_container = QFrame()
        input_container.setStyleSheet(
            "QFrame { "
            "  background: rgba(14, 17, 24, 0.75); "
            "  border: 1px solid rgba(0, 229, 255, 0.35); "
            "  border-radius: 14px; "
            "}"
        )
        input_row = QHBoxLayout(input_container)
        input_row.setContentsMargins(10, 6, 10, 6)
        input_row.setSpacing(8)

        self._attach_btn = QPushButton("📎")
        self._attach_btn.setFixedSize(30, 30)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip("Attach file")
        self._attach_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.WHITE}; border: none; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; }}"
        )
        self._attach_btn.clicked.connect(self.attach_requested.emit)
        input_row.addWidget(self._attach_btn)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Message Brahma Evo...")
        self._input.setFont(QFont("Segoe UI", 10))
        self._input.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {C.WHITE}; border: none; padding: 2px 4px; selection-background-color: rgba(0, 229, 255, 0.25); }}"
        )
        self._input.returnPressed.connect(self._send)
        input_row.addWidget(self._input, 1)

        self._mic_btn = QPushButton("🎙")
        self._mic_btn.setFixedSize(30, 30)
        self._mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_btn.setToolTip("Toggle Voice")
        self._mic_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.WHITE}; border: none; font-size: 14px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; }}"
        )
        self._mic_btn.clicked.connect(self.mic_requested.emit)
        input_row.addWidget(self._mic_btn)

        self._send_btn = QPushButton("➤")
        self._send_btn.setFixedSize(32, 30)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("Send message (Enter)")
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0, 229, 255, 0.25); color: {C.PRI}; border: 1px solid rgba(0, 229, 255, 0.50); border-radius: 8px; font-weight: bold; font-size: 13px; }}"
            f"QPushButton:hover {{ background: rgba(0, 229, 255, 0.45); color: #FFFFFF; border-color: {C.PRI}; }}"
        )
        self._send_btn.clicked.connect(self._send)
        input_row.addWidget(self._send_btn)

        lay.addWidget(input_container)

        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self._history_search = QLineEdit()
        self._history_search.setPlaceholderText("Search conversations...")
        self._history_search.setFont(QFont("Segoe UI", 10))
        self._history_search.setFixedHeight(38)
        self._history_search.setStyleSheet(
            """
            QLineEdit {
                background: rgba(10,11,14,205);
                color: #FFFFFF;
                border: 1px solid rgba(0, 229, 255,100);
                border-radius: 12px;
                padding: 0 12px;
            }
            QLineEdit:focus {
                border: 1px solid rgba(0, 229, 255,190);
            }
            """
        )
        self._history_search.textChanged.connect(self._refresh_history)
        lay.addWidget(self._history_search)

        self._history_scroll = QScrollArea()
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._history_scroll.setStyleSheet(
            """
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                border: none;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 229, 255,0.45);
                border-radius: 4px;
                min-height: 24px;
            }
            """
        )
        self._history_content = QWidget()
        self._history_content.setStyleSheet("background: transparent;")
        self._history_layout = QVBoxLayout(self._history_content)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(10)
        self._history_layout.addStretch(1)
        self._history_scroll.setWidget(self._history_content)
        lay.addWidget(self._history_scroll, 1)
        return page

    def _ensure_active_conversation(self, first_message: str | None = None) -> str:
        convo_id = self._active_conversation_id
        if convo_id:
            convo = self._store.get_conversation(convo_id)
            if convo:
                return convo_id
        convo_id = self._store.ensure_active_conversation(first_message or "")
        self._active_conversation_id = convo_id
        return convo_id

    def _group_label(self, title: str) -> QLabel:
        lbl = QLabel(title.upper())
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet("color: rgba(255,255,255,0.58); background: transparent; letter-spacing: 1px;")
        return lbl

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_history(self, *_):
        search = self._history_search.text().strip() if hasattr(self, "_history_search") else ""
        self._clear_layout(self._history_layout)
        groups = self._store.grouped_conversations(search)
        current = self._active_conversation_id
        if not groups:
            empty = QLabel("No conversations yet.")
            empty.setStyleSheet("color: rgba(255,255,255,0.60); background: transparent;")
            self._history_layout.addWidget(empty)
            self._history_layout.addStretch(1)
            return
        for group_name, items in groups.items():
            self._history_layout.addWidget(self._group_label(group_name))
            for item in items:
                widget = HistoryConversationItem(
                    item["id"],
                    item["title"],
                    _fmt_time_stamp(item["updatedAt"]),
                    pinned=bool(item["pinned"]),
                )
                if item["id"] == current:
                    widget.setStyleSheet(
                        """
                        QFrame#HistoryConversationItem {
                            background: rgba(0, 229, 255,0.10);
                            border: 1px solid rgba(0, 229, 255,0.55);
                            border-radius: 10px;
                        }
                        QFrame#HistoryConversationItem:hover {
                            background: rgba(0, 229, 255,0.14);
                            border: 1px solid rgba(0, 229, 255,0.70);
                        }
                        """
                    )
                widget.clicked.connect(self._load_conversation)
                self._history_layout.addWidget(widget)
            self._history_layout.addSpacing(4)
        self._history_layout.addStretch(1)

    def _show_memory_banner(self, memories: list[dict[str, object]]):
        texts = [str(m.get("content") or "").strip() for m in memories if str(m.get("content") or "").strip()]
        if not texts:
            self._memory_items.setText("")
            self._memory_frame.hide()
            return
        self._memory_items.setText("• " + "\n• ".join(texts[:4]))
        self._memory_frame.show()

    def _hide_memory_banner(self):
        self._memory_items.setText("")
        self._memory_frame.hide()

    def _load_active_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        if convo:
            self._feed.load_messages(convo.get("messages") or [])
            self._active_conversation_id = convo_id
            self._hide_memory_banner()

    def _load_conversation(self, conversation_id: str):
        convo = self._store.get_conversation(conversation_id)
        if not convo:
            return
        self._active_conversation_id = conversation_id
        self._store.set_active_conversation_id(conversation_id)
        self._feed.load_messages(convo.get("messages") or [])
        self._hide_memory_banner()
        self._refresh_history()
        self._set_tab(0)

    def _new_conversation(self):
        self._active_conversation_id = self._store.create_conversation("New Conversation")
        self._feed.clear_messages()
        self._hide_memory_banner()
        self._refresh_history()
        self._set_tab(0)

    def _clear_current_conversation(self):
        self._new_conversation()

    def _rename_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        current = (convo or {}).get("title") or "Conversation"
        title, ok = QInputDialog.getText(self, "Rename Conversation", "Conversation title:", text=current)
        if ok and title.strip():
            self._store.rename_conversation(convo_id, title.strip())
            self._refresh_history()

    def _export_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        title = (convo or {}).get("title") or "conversation"
        default = str(BASE_DIR / "downloads" / f"{title}.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export Conversation", default, "JSON Files (*.json)")
        if not path:
            return
        try:
            self._store.export_conversation(convo_id, path)
        except Exception:
            pass

    def _pin_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        convo = self._store.get_conversation(convo_id)
        pinned = not bool((convo or {}).get("pinned"))
        self._store.pin_conversation(convo_id, pinned)
        self._refresh_history()

    def _delete_current_conversation(self):
        convo_id = self._ensure_active_conversation()
        self._store.delete_conversation(convo_id)
        self._active_conversation_id = None
        self._new_conversation()

    def show_at(self):
        self.show_workspace(animate=False)

    def reposition(self):
        if not self.isVisible():
            return
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self._expanded_w + 1
        y = screen.top() + 18
        h = max(640, screen.height() - 36)
        self.setGeometry(x, y, self._expanded_w, h)
        self._target_h = h
        self._panel.setGeometry(0, 0, self._expanded_w, h)

    def _dock_rect(self) -> QRectF:
        screen = QApplication.primaryScreen().availableGeometry()
        h = max(640, screen.height() - 36)
        y = screen.top() + 18
        w = self._expanded_w
        x = screen.right() - w + 1
        return QRectF(x, y, w, h)

    def _collapsed_rect(self) -> QRectF:
        screen = QApplication.primaryScreen().availableGeometry()
        h = max(640, screen.height() - 36)
        y = screen.top() + 18
        w = self._expanded_w
        x = screen.right() + 8
        return QRectF(x, y, w, h)

    def focus_input(self):
        self._set_tab(0)
        if hasattr(self, "_input") and self._input.isVisible():
            self._input.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_workspace(self, animate: bool = True):
        self._collapsed = False
        self._panel.show()
        self._content.show()
        dock = self._dock_rect()
        start = self._collapsed_rect() if animate else dock
        self.setGeometry(start.toRect())
        self.show()
        self.raise_()
        self.activateWindow()
        self._apply_state()
        if animate:
            self._anim.stop()
            self._anim.setStartValue(start.toRect())
            self._anim.setEndValue(dock.toRect())
            self._anim.start()
        else:
            self.setGeometry(dock.toRect())
        self._panel.setGeometry(0, 0, self.width(), self.height())
        QTimer.singleShot(100, self.focus_input)

    def hide_workspace(self, animate: bool = True):
        self._collapsed = True
        if not self.isVisible():
            self._panel.hide()
            self.hide()
            return
        if animate:
            dock = self.geometry()
            end = self._collapsed_rect()
            self._anim.stop()
            self._anim.setStartValue(dock)
            self._anim.setEndValue(end.toRect())
            self._anim.finished.connect(self._hide_after_anim)
            self._anim.start()
        else:
            self._hide_after_anim()

    def _hide_after_anim(self):
        try:
            self._anim.finished.disconnect(self._hide_after_anim)
        except Exception:
            pass
        self._panel.hide()
        self.hide()

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool):
        if bool(collapsed):
            self.hide_workspace(animate=True)
        else:
            self.show_workspace(animate=True)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _apply_state(self):
        self._panel.setVisible(not self._collapsed)
        self._content.setVisible(not self._collapsed)
        if self.isVisible() and not self._collapsed:
            self.reposition()

    def append_log(self, text: str):
        raw = (text or "").strip()
        if not raw:
            return
        low = raw.lower()
        if low.startswith(("you:", "brahma evo:")):
            return
        if low.startswith("sys:"):
            self.record_chat_event({"role": "system", "text": raw.split(":", 1)[1].strip(), "source": "local"})

    def record_chat_event(self, event: object):
        data = event if isinstance(event, dict) else {}
        role = (data.get("role") or "").strip().lower()
        text = (data.get("text") or data.get("content") or "").strip()
        if not role or not text:
            return
        attachments = data.get("attachments") or []
        stamp = data.get("timestamp")
        convo_id = data.get("conversation_id") or self._active_conversation_id
        if role == "user":
            convo_id = self._store.record_chat("user", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("user", "You", text, _fmt_time_stamp(stamp), attachments=attachments)
            memories = self._store.search_memories(text)
            self._show_memory_banner(memories)
        elif role == "assistant":
            convo_id = self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("assistant", "Brahma Evo", text, _fmt_time_stamp(stamp), attachments=attachments, animate=True)
            self._hide_memory_banner()
        elif role == "system":
            convo_id = self._store.record_chat("system", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("system", "System", text, _fmt_time_stamp(stamp), attachments=attachments, event_type=text)
        elif role == "file":
            convo_id = self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._active_conversation_id = convo_id
            self._feed.add_message("file", "Files", text, _fmt_time_stamp(stamp), attachments=attachments)
        self._refresh_history()

    def apply_task_workspace(self, event: object):
        data = event if isinstance(event, dict) else {}
        action = (data.get("action") or "update").strip().lower()
        if action == "start":
            command = data.get("command") or ""
            plan = data.get("plan") or []
            self._task_card.show()
            self._task_card.start_workspace(command, plan, data.get("source") or "local")
            # Intentionally do not post a 'Task started' system message to the activity feed
            # because the workspace UI already shows the task status.
        elif action == "update":
            self._task_card.show()
            self._task_card.update_workspace(
                title=data.get("title"),
                command=data.get("command"),
                plan=data.get("plan"),
                status=data.get("status"),
                output=data.get("output"),
                percent=data.get("percent"),
                footer=data.get("footer"),
            )
            chunks = [data.get("status") or "", data.get("output") or "", data.get("footer") or ""]
            text = "\n".join(chunk for chunk in chunks if chunk)
            if text:
                self.record_chat_event({"role": "system", "text": text, "source": data.get("source") or "local"})
        elif action == "finish":
            self._task_card.show()
            self._task_card.finish_workspace(
                data.get("result") or data.get("output") or "Done.",
                data.get("status") or "Task completed.",
                int(data.get("percent") or 100),
            )
            self.record_chat_event({
                "role": "system",
                "text": data.get("result") or data.get("output") or "Done.",
                "source": data.get("source") or "local",
            })
            QTimer.singleShot(4000, self._task_card.hide)
        elif action == "clear":
            self._task_card.clear_workspace()

    def _send(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._ensure_active_conversation(text)
        self.command_submitted.emit(text)


class InlineChatWorkspace(QFrame):
    command_submitted = pyqtSignal(str)
    attach_requested = pyqtSignal()
    mic_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("InlineChatWorkspace")
        self._store = workspace_store()
        self._active_conversation_id: str | None = None
        self.setStyleSheet(
            """
            QFrame#InlineChatWorkspace {
                background: transparent;
                border: none;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 4)
        tabs.setSpacing(8)
        self._chat_btn = self._mk_tab("CHAT", True)
        self._history_btn = self._mk_tab("HISTORY", False)
        self._chat_btn.clicked.connect(lambda: self._set_tab(0))
        self._history_btn.clicked.connect(lambda: self._set_tab(1))
        tabs.addWidget(self._chat_btn)
        tabs.addWidget(self._history_btn)

        self._live_mode_btn = QPushButton("Mode: Always On")
        self._live_mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._live_mode_btn.setFixedHeight(30)
        self._live_mode_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._live_mode_btn.setStyleSheet("background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.6); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 0 10px;")

        self._live_call_btn = QPushButton("📞 Connect")
        self._live_call_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._live_call_btn.setFixedHeight(30)
        self._live_call_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._live_call_btn.setStyleSheet("background: rgba(0, 229, 255, 0.15); color: #00e5ff; border: 1px solid #00e5ff; border-radius: 8px; padding: 0 12px;")
        self._live_call_btn.hide()

        tabs.addStretch(1)
        tabs.addWidget(self._live_mode_btn)
        tabs.addWidget(self._live_call_btn)

        self._live_mode = "always_on"
        self._call_active = False
        self._live_mode_btn.clicked.connect(self._toggle_mode)
        self._live_call_btn.clicked.connect(self._toggle_call)

        root.addLayout(tabs)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_chat_tab())
        self._stack.addWidget(self._build_history_tab())
        root.addWidget(self._stack, 1)

        self._set_tab(0)
        self._ensure_conversation()
        self._load_active_conversation()
        self._refresh_history()

    def _toggle_mode(self):
        if self._live_mode == "always_on":
            self._live_mode = "on_call"
            self._live_mode_btn.setText("Mode: On Call")
            self._live_call_btn.show()
        else:
            self._live_mode = "always_on"
            self._live_mode_btn.setText("Mode: Always On")
            self._call_active = False
            self._live_call_btn.setText("📞 Connect")
            self._live_call_btn.setStyleSheet("background: rgba(0, 229, 255, 0.15); color: #00e5ff; border: 1px solid #00e5ff; border-radius: 8px; padding: 0 12px;")
            self._live_call_btn.hide()

        p = self.parentWidget()
        while p and not hasattr(p, "live_mode_changed"):
            p = p.parentWidget()
        if p:
            p.live_mode_changed.emit(self._live_mode)

    def _toggle_call(self):
        self._call_active = not self._call_active
        if self._call_active:
            self._live_call_btn.setText("🔴 End Call")
            self._live_call_btn.setStyleSheet("background: rgba(255, 59, 48, 0.15); color: #ff3b30; border: 1px solid #ff3b30; border-radius: 8px; padding: 0 12px;")
        else:
            self._live_call_btn.setText("📞 Connect")
            self._live_call_btn.setStyleSheet("background: rgba(0, 229, 255, 0.15); color: #00e5ff; border: 1px solid #00e5ff; border-radius: 8px; padding: 0 12px;")

        p = self.parentWidget()
        while p and not hasattr(p, "live_call_toggled"):
            p = p.parentWidget()
        if p:
            p.live_call_toggled.emit(self._call_active)

    def _mk_tab(self, text: str, active: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(34)
        btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        btn.setStyleSheet(
            """
            QPushButton {
                background: rgba(255,255,255,0.04);
                color: rgba(255,255,255,0.82);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255,0.08);
                border: 1px solid rgba(0, 229, 255,0.30);
            }
            """
        )
        return btn

    def _set_tab(self, index: int):
        self._stack.setCurrentIndex(0 if index == 0 else 1)
        self._chat_btn.setChecked(index == 0)
        self._history_btn.setChecked(index == 1)
        if index == 0:
            self._chat_btn.setStyleSheet("""
                QPushButton { background: rgba(0, 229, 255,0.16); color: #FFFFFF; border: 1px solid rgba(0, 229, 255,180); border-radius: 10px; padding: 0 14px; }
            """)
            self._history_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.82); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 0 14px; }
                QPushButton:hover { background: rgba(0, 229, 255,0.08); border: 1px solid rgba(0, 229, 255,0.30); }
            """)
        else:
            self._history_btn.setStyleSheet("""
                QPushButton { background: rgba(0, 229, 255,0.16); color: #FFFFFF; border: 1px solid rgba(0, 229, 255,180); border-radius: 10px; padding: 0 14px; }
            """)
            self._chat_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.82); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 0 14px; }
                QPushButton:hover { background: rgba(0, 229, 255,0.08); border: 1px solid rgba(0, 229, 255,0.30); }
            """)

    def _build_chat_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        today_bar = QHBoxLayout()
        today_bar.addStretch()
        today_pill = QLabel("Today")
        today_pill.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        today_pill.setStyleSheet("""
            QLabel {
                background: rgba(0, 229, 255, 0.12);
                color: rgba(255, 255, 255, 0.85);
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 12px;
                padding: 4px 14px;
            }
        """)
        today_bar.addWidget(today_pill)
        today_bar.addStretch()
        lay.addLayout(today_bar)

        self._task_card = TaskCard()
        self._task_card.hide()
        lay.addWidget(self._task_card)

        self._memory_frame = QFrame()
        self._memory_frame.setVisible(False)
        self._memory_frame.setStyleSheet("""
            QFrame {
                background: rgba(0, 229, 255,0.05);
                border: 1px solid rgba(0, 229, 255,0.24);
                border-radius: 12px;
            }
        """)
        mlay = QVBoxLayout(self._memory_frame)
        mlay.setContentsMargins(12, 10, 12, 10)
        mlay.setSpacing(6)
        title = QLabel("Using Memory:")
        title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title.setStyleSheet("color: #FFFFFF; background: transparent;")
        self._memory_lbl = QLabel("")
        self._memory_lbl.setWordWrap(True)
        self._memory_lbl.setStyleSheet("color: rgba(255,255,255,0.75); background: transparent;")
        mlay.addWidget(title)
        mlay.addWidget(self._memory_lbl)
        lay.addWidget(self._memory_frame)

        self._feed = ConversationFeed()
        lay.addWidget(self._feed, 1)

        input_container = QFrame()
        input_container.setStyleSheet(
            "QFrame { "
            "  background: rgba(14, 17, 24, 0.40); "
            "  border: 1px solid rgba(0, 229, 255, 0.32); "
            "  border-radius: 12px; "
            "}"
        )
        input_row = QHBoxLayout(input_container)
        input_row.setContentsMargins(8, 6, 8, 6)
        input_row.setSpacing(6)

        self._attach_btn = QPushButton("📎")
        self._attach_btn.setFixedSize(28, 28)
        self._attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._attach_btn.setToolTip("Attach file")
        self._attach_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.WHITE}; border: none; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; }}"
        )
        self._attach_btn.clicked.connect(self.attach_requested.emit)
        input_row.addWidget(self._attach_btn)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Message Brahma Evo...")
        self._input.setFont(QFont("Segoe UI", 10))
        self._input.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {C.WHITE}; border: none; padding: 0 4px; selection-background-color: rgba(0, 229, 255, 0.25); }}"
        )
        self._input.returnPressed.connect(self._send)
        input_row.addWidget(self._input, 1)

        self._mic_btn = QPushButton("🎙")
        self._mic_btn.setFixedSize(28, 28)
        self._mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_btn.setToolTip("Toggle Voice")
        self._mic_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.WHITE}; border: none; font-size: 13px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; }}"
        )
        self._mic_btn.clicked.connect(self.mic_requested.emit)
        input_row.addWidget(self._mic_btn)

        self._send_btn = QPushButton("➤")
        self._send_btn.setFixedSize(30, 28)
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setToolTip("Send message")
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0, 229, 255, 0.22); color: {C.PRI}; border: 1px solid rgba(0, 229, 255, 0.45); border-radius: 7px; font-weight: bold; font-size: 13px; }}"
            f"QPushButton:hover {{ background: rgba(0, 229, 255, 0.40); color: #FFFFFF; border-color: {C.PRI}; }}"
        )
        self._send_btn.clicked.connect(self._send)
        input_row.addWidget(self._send_btn)

        lay.addWidget(input_container)

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 4, 2)
        self._footer_status = QLabel("Brahma Evo is ready")
        self._footer_status.setFont(QFont("Segoe UI", 8))
        self._footer_status.setStyleSheet("color: rgba(255, 255, 255, 0.55); background: transparent;")
        footer.addWidget(self._footer_status)
        footer.addStretch(1)

        lay.addLayout(footer)

        return page

    def set_audio_level(self, level: float):
        pass

    def set_voice_status(self, backend: str = "", live: bool | None = None,
                          detail: str = ""):
        """Report which voice backend is driving the session, and whether it is up.

        The chat/footer indicator used to only ever reflect the microphone, so a
        local voice session, a Gemini Live socket and a custom endpoint all
        looked identical - and "listening" could mean anything. Connectivity is
        now stated explicitly and is independent of which backend is selected.
        """
        if backend:
            self._voice_backend = str(backend).strip()
        if live is not None:
            self._voice_live = bool(live)
            # A live/not-live report replaces the detail wholesale, otherwise a
            # previous "standby"/"reconnecting" string keeps showing next to a
            # session that is now connected.
            self._voice_detail = str(detail or "").strip()
        elif detail:
            self._voice_detail = str(detail).strip()
        try:
            self._refresh_connectivity_indicator()
        except Exception:
            pass

    def _connectivity_text(self) -> str:
        backend = getattr(self, "_voice_backend", "") or ""
        live = bool(getattr(self, "_voice_live", False))
        detail = getattr(self, "_voice_detail", "") or ""
        if not backend:
            return detail or "no voice session"
        if live:
            return f"{backend} connected" + (f" - {detail}" if detail else "")
        return f"{backend} not connected"

    def _refresh_connectivity_indicator(self):
        """Re-render the footer with the current state plus real connectivity."""
        if not getattr(self, "_footer_status", None):
            return
        state = (getattr(self, "_state", "") or "").lower()
        base = {
            "listening": "Listening to your voice...",
            "speaking": "Brahma Evo is speaking...",
            "thinking": "Synthesizing response...",
            "executing": "Executing task...",
            "working": "Processing request...",
            "muted": "Microphone muted",
            # Not On-Call-specific any more: standby can be an explicit
            # Disconnect in either mode, on either backend.
            "standby": "Standby - no session",
        }.get(state, "Brahma Evo is ready")
        self._footer_status.setText(f"{base} - {self._connectivity_text()}")

    def set_state(self, state: str):
        if hasattr(self, "_footer_status") and self._footer_status:
            self._state = (state or "").strip().lower()
            self._refresh_connectivity_indicator()

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search conversations...")
        self._search.setFont(QFont("Segoe UI", 10))
        self._search.setFixedHeight(38)
        self._search.setStyleSheet("QLineEdit { background: rgba(10,11,14,205); color: #FFFFFF; border: 1px solid rgba(0, 229, 255,100); border-radius: 12px; padding: 0 12px; }")
        self._search.textChanged.connect(self._refresh_history)
        lay.addWidget(self._search)
        self._history_scroll = QScrollArea()
        self._history_scroll.setWidgetResizable(True)
        self._history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._history_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._history_content = QWidget()
        self._history_content.setStyleSheet("background: transparent;")
        self._history_layout = QVBoxLayout(self._history_content)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(10)
        self._history_layout.addStretch(1)
        self._history_scroll.setWidget(self._history_content)
        lay.addWidget(self._history_scroll, 1)
        return page

    def _ensure_conversation(self, first_message: str | None = None):
        if self._active_conversation_id:
            convo = self._store.get_conversation(self._active_conversation_id)
            if convo:
                return self._active_conversation_id
        self._active_conversation_id = self._store.ensure_active_conversation(first_message or "")
        return self._active_conversation_id

    def _clear_layout(self, layout: QVBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_history(self, *_):
        search = self._search.text().strip() if hasattr(self, "_search") else ""
        self._clear_layout(self._history_layout)
        groups = self._store.grouped_conversations(search)
        if not groups:
            empty = QLabel("No conversations yet.")
            empty.setStyleSheet("color: rgba(255,255,255,0.60); background: transparent;")
            self._history_layout.addWidget(empty)
            self._history_layout.addStretch(1)
            return
        for group_name, items in groups.items():
            self._history_layout.addWidget(self._group_label(group_name))
            for item in items:
                card = HistoryConversationItem(item["id"], item["title"], _fmt_time_stamp(item["updatedAt"]), pinned=bool(item["pinned"]))
                card.clicked.connect(self._open_conversation)
                self._history_layout.addWidget(card)
        self._history_layout.addStretch(1)

    def _group_label(self, title: str) -> QLabel:
        lbl = QLabel(title.upper())
        lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        lbl.setStyleSheet("color: rgba(255,255,255,0.58); background: transparent; letter-spacing: 1px;")
        return lbl

    def _show_memories(self, memories: list[dict[str, object]]):
        texts = [str(m.get("content") or "").strip() for m in memories if str(m.get("content") or "").strip()]
        if not texts:
            self._memory_lbl.setText("")
            self._memory_frame.hide()
            return
        self._memory_lbl.setText("• " + "\n• ".join(texts[:4]))
        self._memory_frame.show()

    def _hide_memories(self):
        self._memory_lbl.setText("")
        self._memory_frame.hide()

    def _open_conversation(self, conversation_id: str):
        convo = self._store.get_conversation(conversation_id)
        if not convo:
            return
        self._active_conversation_id = conversation_id
        self._store.set_active_conversation_id(conversation_id)
        self._feed.load_messages(convo.get("messages") or [])
        self._hide_memories()
        self._refresh_history()
        self._set_tab(0)

    def _load_active_conversation(self):
        convo_id = self._ensure_conversation()
        convo = self._store.get_conversation(convo_id)
        if convo:
            self._feed.load_messages(convo.get("messages") or [])
        self._refresh_history()

    def record_chat_event(self, event: object):
        data = event if isinstance(event, dict) else {}
        role = (data.get("role") or "").strip().lower()
        text = (data.get("text") or data.get("content") or "").strip()
        if not role or not text:
            return

        now = time.time()
        if not hasattr(self, "_last_chat_events"):
            self._last_chat_events = []
        for prev_role, prev_text, prev_time in self._last_chat_events[-6:]:
            if prev_role == role and prev_text == text and (now - prev_time < 2.5):
                return
        self._last_chat_events.append((role, text, now))
        if len(self._last_chat_events) > 20:
            self._last_chat_events = self._last_chat_events[-10:]

        convo_id = data.get("conversation_id") or self._ensure_conversation(text if role == "user" else None)
        attachments = data.get("attachments") or []
        stamp = _fmt_time_stamp(data.get("timestamp"))
        if role == "user":
            self._store.record_chat("user", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("user", "You", text, stamp, attachments=attachments)
            self._show_memories(self._store.search_memories(text))
        elif role == "assistant":
            self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("assistant", "Brahma Evo", text, stamp, attachments=attachments)
            self._hide_memories()
        elif role == "system":
            self._store.record_chat("system", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("system", "System", text, stamp, attachments=attachments)
        elif role == "file":
            self._store.record_chat("assistant", text, conversation_id=convo_id, attachments=attachments)
            self._feed.add_message("file", "Files", text, stamp, attachments=attachments)
        self._refresh_history()

    def append_log(self, text: str):
        raw = (text or "").strip()
        if not raw:
            return
        low = raw.lower()
        if low.startswith("you:"):
            self.record_chat_event({"role": "user", "text": raw.split(":", 1)[1].strip()})
        elif low.startswith("brahma evo:"):
            self.record_chat_event({"role": "assistant", "text": raw.split(":", 1)[1].strip()})
        elif low.startswith("sys:"):
            self.record_chat_event({"role": "system", "text": raw.split(":", 1)[1].strip()})
        elif low.startswith("file:"):
            self.record_chat_event({"role": "file", "text": raw.split(":", 1)[1].strip()})
    def apply_task_workspace(self, event: object):
        data = event if isinstance(event, dict) else {}
        action = (data.get("action") or "update").strip().lower()
        if action == "start":
            command = data.get("command") or ""
            plan = data.get("plan") or []
            self._task_card.start_workspace(command, plan, data.get("source") or "local")
            # Do not emit a 'Task started' system event into the conversation feed.
        elif action == "update":
            self._task_card.update_workspace(
                title=data.get("title"),
                command=data.get("command"),
                plan=data.get("plan"),
                status=data.get("status"),
                output=data.get("output"),
                percent=data.get("percent"),
                footer=data.get("footer"),
            )
            chunks = [data.get("status") or "", data.get("output") or "", data.get("footer") or ""]
            text = "\n".join(chunk for chunk in chunks if chunk)
            if text:
                self.record_chat_event({"role": "system", "text": text, "source": data.get("source") or "local"})
        elif action == "finish":
            self._task_card.finish_workspace(
                data.get("result") or data.get("output") or "Done.",
                data.get("status") or "Task completed.",
                int(data.get("percent") or 100),
            )
            self.record_chat_event({
                "role": "system",
                "text": data.get("result") or data.get("output") or "Done.",
                "source": data.get("source") or "local",
            })
            QTimer.singleShot(4000, self._task_card.hide)
        elif action == "clear":
            self._task_card.clear_workspace()

    def _send(self):
        text = self._input.text().strip() if hasattr(self, "_input") else ""
        if not text:
            return
        self._input.clear()
        convo_id = self._ensure_conversation(text)
        self.record_chat_event({"role": "user", "text": text, "conversation_id": convo_id})
        self.command_submitted.emit(text)

    def focus_input(self):
        if hasattr(self, "_input"):
            self._input.setFocus()

    def new_conversation(self):
        self._active_conversation_id = self._store.create_conversation("New Conversation")
        self._feed.load_messages([])
        self._hide_memories()
        self._refresh_history()
        self._set_tab(0)
        self.focus_input()
