"""Floating launcher and the floating gesture-card overlay, moved verbatim
out of ui.py (P4-19). GestureCameraPreview is imported directly from
core.ui_gesture; Qt and stdlib imports are direct."""
from __future__ import annotations

from PyQt6.QtCore import (QPoint, QPointF, QRectF, QTimer, Qt, pyqtSignal)
from PyQt6.QtGui import (QAction, QBrush, QColor, QFont, QPainter, QPen, QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QMenu, QPushButton, QVBoxLayout, QWidget)
from core.ui_gesture import (GestureCameraPreview)

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
        menu.setStyleSheet("""
            QMenu {
                background: rgba(14, 16, 22, 248);
                color: #FFFFFF;
                border: 1px solid rgba(0, 229, 255, 0.45);
                border-radius: 12px;
                padding: 6px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QMenu::item {
                padding: 7px 18px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: rgba(0, 229, 255, 0.20);
                color: #00e5ff;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 0.10);
                margin: 4px 6px;
            }
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
