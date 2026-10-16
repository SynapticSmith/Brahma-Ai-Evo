"""Transient UI overlays (scanning, boot sequence, incoming alert,
meeting) plus the two easing helpers they share, moved verbatim out of
ui.py (P4-19). Qt/stdlib/core imports are direct; np/cv2 stay
undeclared as they are in ui.py (soft-fail path)."""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import (QPointF, QRectF, QTimer, Qt, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)
from core.ui_theme import (C)

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
