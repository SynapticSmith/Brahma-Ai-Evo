"""Logo/branding + icon painters, moved verbatim out of ui.py (P4-19).
Qt/core imports are direct; LOGO_FILE and LOGO_ICO stay in ui.py and are
injected via bind() (used only at runtime)."""
from __future__ import annotations

from PyQt6.QtCore import (QPointF, QRectF, Qt)
from PyQt6.QtGui import (QColor, QIcon, QPainter, QPen, QPixmap)
from PyQt6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QWidget)
from core.ui_theme import (C)


def bind(*, LOGO_FILE, LOGO_ICO):
    globals().update(LOGO_FILE=LOGO_FILE, LOGO_ICO=LOGO_ICO)

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
