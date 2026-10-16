"""Smart-home devices section and the phone/tab "Connect Devices" page,
moved verbatim out of ui.py (P4-19). Shared project symbols (C,
APP_SETTINGS_FILE) are injected by ui.py through bind(); Qt, stdlib and
external service imports are direct."""
from __future__ import annotations

import asyncio
import json

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, QTimer, Qt, pyqtSignal)
from PyQt6.QtGui import (QColor, QFont, QPixmap)
from PyQt6.QtWidgets import (QColorDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget)
from smart_home import (SmartHomeService)
from smart_home_page_new import (_DeviceTile)


def bind(*, APP_SETTINGS_FILE, C):
    globals().update(APP_SETTINGS_FILE=APP_SETTINGS_FILE, C=C)

class SmartDevicesSection(QFrame):
    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._service = SmartHomeService()
        self._snapshot = ""
        self._device_tiles: list[_DeviceTile] = []
        self._card_anims: list[QPropertyAnimation] = []
        self._selected_device: dict[str, object] | None = None

        self.setObjectName("SmartDevicesSection")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(f"""
            QFrame#SmartDevicesSection {{
                background: transparent;
                border: none;
            }}
            QLabel {{
                background: transparent;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self._title_lbl = QLabel("SMART DEVICES")
        self._title_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.WHITE}; letter-spacing: 1px;")
        self._subtitle_lbl = QLabel("Quick access to your connected home devices.")
        self._subtitle_lbl.setFont(QFont("Segoe UI", 8))
        self._subtitle_lbl.setStyleSheet(f"color: {C.TEXT_DIM};")
        title_box.addWidget(self._title_lbl)
        title_box.addWidget(self._subtitle_lbl)
        header.addLayout(title_box, 1)

        self._count_chip = QLabel("0 devices")
        self._count_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_chip.setStyleSheet(
            f"QLabel {{ background: rgba(255,255,255,0.03); color: {C.PRI}; border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; padding: 4px 10px; }}"
        )
        header.addWidget(self._count_chip)

        self._refresh_btn = QPushButton("Γå╗")
        self._refresh_btn.setFixedSize(32, 32)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 9px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255,0.08);
                border: 1px solid {C.PRI};
            }}
        """)
        self._refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        header.addWidget(self._refresh_btn)

        self._open_home_btn = QPushButton("Add Device")
        self._open_home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_home_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255,0.10);
                color: {C.WHITE};
                border: 1px solid {C.PRI};
                border-radius: 9px;
                padding: 0 14px;
                min-height: 32px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255,0.16);
            }}
        """)
        self._open_home_btn.clicked.connect(self._open_brahma_home)
        header.addWidget(self._open_home_btn)
        root.addLayout(header)

        self._empty_card = QWidget()
        self._empty_card.setStyleSheet("background: transparent;")
        empty_lay = QVBoxLayout(self._empty_card)
        empty_lay.setContentsMargins(6, 4, 6, 4)
        empty_lay.setSpacing(6)
        empty_title = QLabel("No Smart Devices Connected")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        empty_title.setStyleSheet(f"color: {C.WHITE}; border: none;")
        empty_desc = QLabel("Connect your first smart device to control it directly from your dashboard.")
        empty_desc.setWordWrap(True)
        empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_desc.setFont(QFont("Segoe UI", 8))
        empty_desc.setStyleSheet(f"color: {C.TEXT_DIM};")
        empty_btn = QPushButton("Open Brahma Evo Home")
        empty_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        empty_btn.setFixedWidth(160)
        empty_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 229, 255,0.12);
                color: {C.WHITE};
                border: 1px solid {C.PRI};
                border-radius: 10px;
                min-height: 34px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255,0.18);
            }}
        """)
        empty_btn.clicked.connect(self._open_brahma_home)
        empty_lay.addStretch(1)
        empty_lay.addWidget(empty_title)
        empty_lay.addWidget(empty_desc)
        empty_lay.addWidget(empty_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_lay.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._grid_page = QWidget()
        self._grid_page.setStyleSheet("background: transparent;")
        self._grid_layout = QGridLayout(self._grid_page)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setHorizontalSpacing(12)
        self._grid_layout.setVerticalSpacing(12)

        self._row_page = QWidget()
        self._row_page.setStyleSheet("background: transparent;")
        self._row_layout = QHBoxLayout(self._row_page)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(12)

        self._cards_stack = QStackedWidget()
        self._cards_stack.setStyleSheet("background: transparent; border: none;")
        self._cards_stack.addWidget(self._grid_page)
        self._cards_stack.addWidget(self._row_page)
        self._scroll.setWidget(self._cards_stack)
        root.addWidget(self._empty_card)
        root.addWidget(self._scroll, 1)

        self._panel = QFrame(self)
        self._panel.setObjectName("SmartDevicePanel")
        self._panel.setStyleSheet(f"""
            QFrame#SmartDevicePanel {{
                background: rgba(8, 9, 13, 245);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 18px;
            }}
            QLabel {{
                background: transparent;
            }}
            QPushButton {{
                background: rgba(255,255,255,0.03);
                color: {C.WHITE};
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                background: rgba(0, 229, 255,0.08);
                border: 1px solid {C.PRI};
            }}
        """)
        self._panel.setVisible(False)
        self._panel.setMinimumSize(280, 260)
        self._panel_effect = QGraphicsOpacityEffect(self._panel)
        self._panel_effect.setOpacity(0.0)
        self._panel.setGraphicsEffect(self._panel_effect)
        self._panel_lay = QVBoxLayout(self._panel)
        self._panel_lay.setContentsMargins(14, 12, 14, 12)
        self._panel_lay.setSpacing(8)

        panel_top = QHBoxLayout()
        panel_top.setSpacing(6)
        self._panel_name = QLabel("Device")
        self._panel_name.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._panel_name.setStyleSheet(f"color: {C.WHITE};")
        panel_top.addWidget(self._panel_name, 1)
        self._panel_close = QPushButton("X")
        self._panel_close.setFixedSize(28, 28)
        self._panel_close.clicked.connect(self._close_panel)
        panel_top.addWidget(self._panel_close)
        self._panel_lay.addLayout(panel_top)

        self._panel_meta = QLabel("")
        self._panel_meta.setWordWrap(True)
        self._panel_meta.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._panel_lay.addWidget(self._panel_meta)

        self._panel_status = QLabel("")
        self._panel_status.setStyleSheet(f"color: {C.GREEN}; font-weight: 700;")
        self._panel_lay.addWidget(self._panel_status)

        self._panel_controls = QVBoxLayout()
        self._panel_controls.setSpacing(10)
        self._panel_lay.addLayout(self._panel_controls)
        self._panel_lay.addStretch(1)

        self._panel_actions = QHBoxLayout()
        self._panel_actions.setSpacing(8)
        self._panel_lay.addLayout(self._panel_actions)

        self._panel_hint = QLabel("")
        self._panel_hint.setWordWrap(True)
        self._panel_hint.setStyleSheet(f"color: {C.TEXT_DIM};")
        self._panel_lay.addWidget(self._panel_hint)

        self._poll_tmr = QTimer(self)
        self._poll_tmr.timeout.connect(lambda: self.refresh(force=False))
        self._poll_tmr.start(2500)

        self.refresh(force=True)

    def _controller_bridge(self):
        return self._controller

    def _open_brahma_home(self):
        bridge = self._controller_bridge()
        if bridge and hasattr(bridge, "_set_page"):
            bridge._set_page("home")

    def _snapshot_devices(self, devices: list[dict[str, object]]) -> str:
        payload = [
            (
                str(d.get("id", "")),
                int(d.get("updated_at") or 0),
                str(d.get("name", "")),
                bool(d.get("is_on")),
                str(d.get("room", "")),
                str(d.get("device_type", "")),
                str(d.get("manufacturer", "")),
            )
            for d in devices
        ]
        return json.dumps(payload, ensure_ascii=True, sort_keys=False)

    def refresh(self, force: bool = False):
        devices = self._service.list_devices()
        snapshot = self._snapshot_devices(devices)
        if not force and snapshot == self._snapshot:
            return
        self._snapshot = snapshot
        self._count_chip.setText(f"{len(devices)} device(s)")
        self._empty_card.setVisible(not devices)
        self._scroll.setVisible(bool(devices))
        self._rebuild_device_cards(devices)
        if self._panel.isVisible() and self._selected_device:
            device_id = str(self._selected_device.get("id", ""))
            device = self._find_device(device_id)
            if device:
                self._selected_device = device
                self._populate_panel(device)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _rebuild_device_cards(self, devices: list[dict[str, object]]):
        self._clear_layout(self._grid_layout)
        self._clear_layout(self._row_layout)
        self._device_tiles.clear()
        self._card_anims.clear()

        if not devices:
            self._cards_stack.setCurrentIndex(0)
            self._close_panel()
            return

        count = len(devices)
        if count <= 3:
            self._cards_stack.setCurrentIndex(1)
            self._clear_layout(self._row_layout)
            for idx, device in enumerate(devices):
                tile = _DeviceTile(device)
                tile.select_requested.connect(self._open_device_panel)
                tile.action_requested.connect(self._apply_tile_action)
                self._row_layout.addWidget(tile)
                self._animate_card(tile, idx)
                self._device_tiles.append(tile)
            self._row_layout.addStretch(1)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        elif count <= 6:
            self._cards_stack.setCurrentIndex(0)
            columns = 3 if count > 3 else count
            for idx, device in enumerate(devices):
                tile = _DeviceTile(device)
                tile.select_requested.connect(self._open_device_panel)
                tile.action_requested.connect(self._apply_tile_action)
                self._grid_layout.addWidget(tile, idx // columns, idx % columns)
                self._animate_card(tile, idx)
                self._device_tiles.append(tile)
            last_row = (count - 1) // columns + 1
            self._grid_layout.setRowStretch(last_row, 1)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self._cards_stack.setCurrentIndex(1)
            self._clear_layout(self._row_layout)
            for idx, device in enumerate(devices):
                tile = _DeviceTile(device)
                tile.select_requested.connect(self._open_device_panel)
                tile.action_requested.connect(self._apply_tile_action)
                self._row_layout.addWidget(tile)
                self._animate_card(tile, idx)
                self._device_tiles.append(tile)
            self._row_layout.addStretch(1)
            self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def _animate_card(self, widget: QWidget, index: int):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._card_anims.append(anim)
        QTimer.singleShot(index * 45, anim.start)

    def _device_id_from_input(self, device_or_id):
        if isinstance(device_or_id, dict):
            return str(device_or_id.get("id", ""))
        return str(device_or_id or "")

    def _find_device(self, device_id: str) -> dict[str, object] | None:
        for device in self._service.list_devices():
            if str(device.get("id", "")) == device_id:
                return device
        return None

    def _apply_tile_action(self, device_id: str, action: str, payload: dict):
        try:
            self._service.execute_device_action(device_id, action, payload)
        except Exception:
            return
        self.refresh(force=True)
        device = self._find_device(device_id)
        if device:
            self._selected_device = device
            self._open_device_panel(device)

    def _open_device_panel(self, device_or_id):
        try:
            device_id = self._device_id_from_input(device_or_id)
            device = self._find_device(device_id)
            if not device:
                return
            self._selected_device = device
            self._populate_panel(device)
            self._show_panel()
        except Exception:
            return

    def _show_panel(self):
        try:
            self._position_panel()
            self._panel.setVisible(True)
            self._panel.raise_()
            self._panel_effect.setOpacity(1.0)
        except Exception:
            self._panel.hide()
            self._panel_effect.setOpacity(0.0)

    def _close_panel(self):
        if not self._panel.isVisible():
            return
        self._panel.hide()
        self._panel_effect.setOpacity(0.0)

    def _clear_panel_controls(self):
        self._clear_layout(self._panel_controls)
        self._clear_layout(self._panel_actions)

    def _populate_panel(self, device: dict[str, object]):
        try:
            self._clear_panel_controls()
            self._clear_layout(self._panel_actions)
            name = str(device.get("name", "Device"))
            room = str(device.get("room", ""))
            device_type = str(device.get("device_type", "device")).lower()
            is_on = bool(device.get("is_on"))
            traits = device.get("traits") if isinstance(device.get("traits"), dict) else {}

            self._panel_name.setText(name)
            self._panel_meta.setText(
                f"Room: {room or 'Unassigned'}\nType: {device_type.title()}\nManufacturer: {device.get('manufacturer', '')}\nConnection: Connected"
            )
            self._panel_status.setText("ON" if is_on else "OFF")
            self._panel_hint.setText("Click outside the panel to close.")

            power_btn = QPushButton("Power")
            power_btn.clicked.connect(lambda: self._apply_tile_action(str(device.get("id", "")), "power", {"is_on": not is_on}))
            self._panel_actions.addWidget(power_btn)

            forget_btn = QPushButton("Forget")
            forget_btn.clicked.connect(lambda: self._forget_device(str(device.get("id", ""))))
            self._panel_actions.addWidget(forget_btn)

            rename_btn = QPushButton("Rename")
            rename_btn.clicked.connect(lambda: self._rename_device(str(device.get("id", "")), name))
            self._panel_actions.addWidget(rename_btn)

            if device_type == "fan":
                speed = int(traits.get("speed", 4) or 4)
                lbl = QLabel(f"Speed {speed}")
                lbl.setStyleSheet(f"color: {C.TEXT_MED};")
                self._panel_controls.addWidget(lbl)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(1, 6)
                slider.setValue(speed)
                slider.valueChanged.connect(lambda value: self._apply_tile_action(str(device.get("id", "")), "speed", {"speed": value}))
                self._panel_controls.addWidget(slider)
            elif device_type == "light":
                brightness = int(traits.get("brightness", 75) or 75)
                lbl = QLabel(f"Brightness {brightness}%")
                lbl.setStyleSheet(f"color: {C.TEXT_MED};")
                self._panel_controls.addWidget(lbl)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(1, 100)
                slider.setValue(brightness)
                slider.valueChanged.connect(lambda value: self._apply_tile_action(str(device.get("id", "")), "brightness", {"brightness": value}))
                self._panel_controls.addWidget(slider)
                color_btn = QPushButton("Color")
                color_btn.clicked.connect(lambda: self._pick_color(device))
                self._panel_controls.addWidget(color_btn)
            elif device_type == "ac":
                temperature = int(traits.get("temperature", 24) or 24)
                lbl = QLabel(f"Temperature {temperature}")
                lbl.setStyleSheet(f"color: {C.TEXT_MED};")
                self._panel_controls.addWidget(lbl)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(16, 30)
                slider.setValue(temperature)
                slider.valueChanged.connect(lambda value: self._apply_tile_action(str(device.get("id", "")), "temperature", {"temperature": value}))
                self._panel_controls.addWidget(slider)
            elif device_type in ("tv", "speaker"):
                volume = int(traits.get("volume", 18) or 18)
                lbl = QLabel(f"Volume {volume}")
                lbl.setStyleSheet(f"color: {C.TEXT_MED};")
                self._panel_controls.addWidget(lbl)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(0, 100)
                slider.setValue(volume)
                slider.valueChanged.connect(lambda value: self._apply_tile_action(str(device.get("id", "")), "volume", {"volume": value}))
                self._panel_controls.addWidget(slider)
            elif device_type == "plug":
                usage = traits.get("energy_usage", traits.get("power_usage", "N/A"))
                lbl = QLabel(f"Energy usage: {usage}")
                lbl.setStyleSheet(f"color: {C.TEXT_MED};")
                self._panel_controls.addWidget(lbl)
            else:
                lbl = QLabel("Primary controls are available from the card below.")
                lbl.setWordWrap(True)
                lbl.setStyleSheet(f"color: {C.TEXT_MED};")
                self._panel_controls.addWidget(lbl)
        except Exception:
            return

    def _pick_color(self, device: dict[str, object]):
        color = QColorDialog.getColor(QColor(C.PRI), self, "Pick Light Color")
        if color.isValid():
            self._apply_tile_action(str(device.get("id", "")), "color", {"color": color.name()})

    def _rename_device(self, device_id: str, current_name: str):
        new_name, ok = QInputDialog.getText(self, "Rename Device", "New name:", text=current_name)
        if ok and new_name.strip():
            try:
                self._service.rename_device(device_id, new_name.strip())
            except Exception:
                return
            self.refresh(force=True)

    def _forget_device(self, device_id: str):
        try:
            self._service.forget_device(device_id)
        except Exception:
            return
        self._close_panel()
        self.refresh(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._panel.isVisible():
            self._position_panel()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh(force=True)

    def _position_panel(self):
        panel_w = min(330, max(270, int(self.width() * 0.28)))
        panel_h = min(340, max(240, int(self.height() * 0.45)))
        x = max(12, self.width() - panel_w - 12)
        y = 12
        self._panel.setGeometry(x, y, panel_w, panel_h)



class _ConnectDeviceCard(QFrame):
    clicked = pyqtSignal(str)
    action_requested = pyqtSignal(str, str, dict)

    def __init__(self, device: dict[str, object], parent=None):
        super().__init__(parent)
        self.device = device
        self._expanded = False
        self.setObjectName("ConnectDeviceCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setMinimumSize(220, 150)
        self.setStyleSheet("""
            QFrame#ConnectDeviceCard {
                background: rgba(10, 12, 18, 240);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 18px;
            }
            QLabel {
                background: transparent;
            }
            QPushButton {
                background: rgba(255,255,255,0.06);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                min-height: 30px;
                padding: 0 10px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255,0.12);
                color: #00e5ff;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self._title = QLabel(str(device.get("name", "Device")))
        self._title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._title.setStyleSheet("color: #ffffff;")
        layout.addWidget(self._title)

        platform = str(device.get("platform", "UNKNOWN")).upper()
        status = "ONLINE" if bool(device.get("online")) else "OFFLINE"
        self._status_label = QLabel(f"{platform} ΓÇó {status}")
        self._status_label.setFont(QFont("Segoe UI", 8))
        self._status_label.setStyleSheet("color: rgba(255,255,255,0.55);")
        layout.addWidget(self._status_label)

        info = []
        if device.get("manufacturer"):
            info.append(str(device.get("manufacturer")))
        if device.get("device_type"):
            info.append(str(device.get("device_type")))
        if device.get("ip"):
            info.append(str(device.get("ip")))
        self._info_lbl = QLabel(" ┬╖ ".join(info) if info else "Connected device")
        self._info_lbl.setFont(QFont("Segoe UI", 8))
        self._info_lbl.setStyleSheet("color: rgba(255,255,255,0.45);")
        self._info_lbl.setWordWrap(True)
        layout.addWidget(self._info_lbl)

        layout.addStretch(1)

        self._action_container = QFrame(self)
        self._action_container.setVisible(False)
        self._action_container.setStyleSheet("QFrame { background: rgba(255,255,255,0.03); border-radius: 14px; }")
        actions_layout = QHBoxLayout(self._action_container)
        actions_layout.setContentsMargins(8, 8, 8, 8)
        actions_layout.setSpacing(8)

        self._rename_btn = QPushButton("Rename")
        self._disconnect_btn = QPushButton("Disconnect")
        self._forget_btn = QPushButton("Remove")
        self._pin_btn = QPushButton("Set PIN")

        actions_layout.addWidget(self._pin_btn)
        actions_layout.addWidget(self._rename_btn)
        actions_layout.addWidget(self._disconnect_btn)
        actions_layout.addWidget(self._forget_btn)
        layout.addWidget(self._action_container)

        self._pin_btn.clicked.connect(lambda: self.action_requested.emit(str(self.device.get("device_id", "")), "set_pin", {}))
        self._rename_btn.clicked.connect(lambda: self.action_requested.emit(str(self.device.get("device_id", "")), "rename", {}))
        self._disconnect_btn.clicked.connect(lambda: self.action_requested.emit(str(self.device.get("device_id", "")), "disconnect", {}))
        self._forget_btn.clicked.connect(lambda: self.action_requested.emit(str(self.device.get("device_id", "")), "forget", {}))

    def set_expanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self._action_container.setVisible(self._expanded)
        if self._expanded:
            self.setStyleSheet("""
                QFrame#ConnectDeviceCard {
                    background: rgba(255,255,255,0.06);
                    border: 1px solid rgba(0, 229, 255,0.25);
                    border-radius: 18px;
                }
                QLabel {
                    background: transparent;
                }
                QPushButton {
                    background: rgba(255,255,255,0.08);
                    color: #ffffff;
                    border: 1px solid rgba(255,255,255,0.12);
                    border-radius: 10px;
                    min-height: 30px;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    background: rgba(0, 229, 255,0.16);
                    color: #00e5ff;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#ConnectDeviceCard {
                    background: rgba(10, 12, 18, 240);
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 18px;
                }
                QLabel {
                    background: transparent;
                }
                QPushButton {
                    background: rgba(255,255,255,0.06);
                    color: #ffffff;
                    border: 1px solid rgba(255,255,255,0.08);
                    border-radius: 10px;
                    min-height: 30px;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    background: rgba(0, 229, 255,0.12);
                    color: #00e5ff;
                }
            """)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(str(self.device.get("device_id", "")))
        super().mouseReleaseEvent(event)


class BrahmaConnectDevicesPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._service = None
        self._connected_device = None
        self._log_snapshot = ""
        self._card_anims: list[QPropertyAnimation] = []
        self._cards: list[_ConnectDeviceCard] = []
        self._selected_device_id: str | None = None
        self._onboarding_known_device_ids: set[str] = set()

        self.setObjectName("BrahmaConnectDevicesPage")
        self.setStyleSheet(f"""
            QFrame#BrahmaConnectDevicesPage {{
                background: transparent;
                border: none;
            }}
            QLabel {{
                background: transparent;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self._title = QLabel("DEVICES")
        self._title.setFont(QFont("Segoe UI", 18, QFont.Weight.Black))
        self._title.setStyleSheet("color: #ffffff; letter-spacing: 2px;")
        self._subtitle = QLabel("Everything connected to Brahma.")
        self._subtitle.setFont(QFont("Segoe UI", 9))
        self._subtitle.setStyleSheet("color: rgba(255,255,255,0.62);")
        title_box.addWidget(self._title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)

        status_wrap = QVBoxLayout()
        status_wrap.setSpacing(4)
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self._gateway_pill = QLabel("ΓùÅ Gateway Online")
        self._gateway_pill.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._gateway_pill.setStyleSheet("color: #35ff75; background: rgba(53,255,117,0.06); border: 1px solid rgba(53,255,117,0.16); border-radius: 12px; padding: 5px 10px;")
        top_row.addWidget(self._gateway_pill)
        top_row.addStretch(1)
        self._add_btn = QPushButton("+ Add Device")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setFixedHeight(34)
        self._add_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._add_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.10);
                color: #ffffff;
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 12px;
                padding: 0 14px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.16);
                border: 1px solid rgba(0, 229, 255, 0.40);
            }
        """)
        self._add_btn.clicked.connect(self._trigger_add_device)
        top_row.addWidget(self._add_btn)
        status_wrap.addLayout(top_row)
        self._gateway_meta = QLabel("Port: 8765  ┬╖  Devices: 0")
        self._gateway_meta.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._gateway_meta.setFont(QFont("Segoe UI", 8))
        self._gateway_meta.setStyleSheet("color: rgba(255,255,255,0.42);")
        status_wrap.addWidget(self._gateway_meta)
        header.addLayout(status_wrap)
        root.addLayout(header)

        self._main_stack = QStackedWidget()
        self._main_stack.setStyleSheet("background: transparent;")
        
        # ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        # PAGE 0 ΓÇö OVERVIEW (GRID / EMPTY)
        # ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        self._overview_page = QWidget()
        ov_lay = QVBoxLayout(self._overview_page)
        ov_lay.setContentsMargins(0, 10, 0, 0)
        ov_lay.setSpacing(12)
        
        self._my_devices_lbl = QLabel("MY DEVICES")
        self._my_devices_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._my_devices_lbl.setStyleSheet("color: #ffffff; letter-spacing: 1px;")
        ov_lay.addWidget(self._my_devices_lbl)
        
        self._overview_stack = QStackedWidget()
        
        # Grid View
        self._grid_page = QWidget()
        grid_lay = QVBoxLayout(self._grid_page)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(16)
        self._scroll.setWidget(self._grid_host)
        grid_lay.addWidget(self._scroll)
        self._overview_stack.addWidget(self._grid_page)
        
        # Empty View
        self._empty_page = QWidget()
        empty_lay = QVBoxLayout(self._empty_page)
        empty_lay.addStretch(1)
        empty_lbl = QLabel("No devices connected yet.")
        empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lbl.setFont(QFont("Segoe UI", 12))
        empty_lbl.setStyleSheet("color: rgba(255,255,255,0.5);")
        empty_lay.addWidget(empty_lbl)
        empty_btn = QPushButton("+ ADD DEVICE")
        empty_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        empty_btn.setFixedSize(160, 40)
        empty_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        empty_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.10);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.25);
                border-radius: 20px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.20);
                border: 1px solid rgba(0, 229, 255, 0.50);
            }
        """)
        empty_btn.clicked.connect(self._trigger_add_device)
        empty_lay.addWidget(empty_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_lay.addStretch(1)
        self._overview_stack.addWidget(self._empty_page)
        
        ov_lay.addWidget(self._overview_stack)
        self._main_stack.addWidget(self._overview_page)
        
        # ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        # PAGE 1 ΓÇö DETAIL VIEW
        # ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        self._detail_page = QWidget()
        det_lay = QVBoxLayout(self._detail_page)
        det_lay.setContentsMargins(0, 10, 0, 0)
        det_lay.setSpacing(16)
        
        back_btn = QPushButton("ΓåÉ Back to Devices")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.setFixedSize(140, 32)
        back_btn.setFont(QFont("Segoe UI", 9))
        back_btn.setStyleSheet("""
            QPushButton { background: transparent; color: rgba(255,255,255,0.7); border: none; text-align: left; }
            QPushButton:hover { color: #00e5ff; }
        """)
        back_btn.clicked.connect(self._close_detail)
        det_lay.addWidget(back_btn)
        
        self._detail_header = QHBoxLayout()
        self._detail_icon = QLabel()
        self._detail_icon.setFixedSize(48, 48)
        self._detail_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_icon.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self._detail_icon.setStyleSheet("background: rgba(0, 229, 255, 0.10); color: #00e5ff; border: 1px solid rgba(0, 229, 255,0.22); border-radius: 12px;")
        self._detail_header.addWidget(self._detail_icon)
        
        det_titles = QVBoxLayout()
        self._detail_title = QLabel()
        self._detail_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self._detail_title.setStyleSheet("color: #ffffff;")
        det_titles.addWidget(self._detail_title)
        
        self._detail_status = QLabel()
        self._detail_status.setFont(QFont("Segoe UI", 10))
        self._detail_status.setStyleSheet("color: rgba(255,255,255,0.6);")
        det_titles.addWidget(self._detail_status)
        self._detail_header.addLayout(det_titles, 1)
        det_lay.addLayout(self._detail_header)
        
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._detail_content = QWidget()
        self._detail_content_lay = QVBoxLayout(self._detail_content)
        self._detail_content_lay.setContentsMargins(0, 10, 0, 10)
        self._detail_content_lay.setSpacing(16)
        
        # Meta info
        self._detail_meta = QLabel()
        self._detail_meta.setStyleSheet("color: rgba(255,255,255,0.7);")
        self._detail_content_lay.addWidget(self._detail_meta)
        
        # Controls
        self._action_box = QFrame()
        self._action_box.setStyleSheet("QFrame { background: rgba(10, 12, 18, 220); border: 1px solid rgba(255,255,255,0.06); border-radius: 16px; }")
        action_lay = QVBoxLayout(self._action_box)
        action_lay.setContentsMargins(16, 16, 16, 16)
        action_title = QLabel("CONTROLS")
        action_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        action_title.setStyleSheet("color: #ffffff; letter-spacing: 1px;")
        action_lay.addWidget(action_title)
        self._action_buttons = QGridLayout()
        self._action_buttons.setSpacing(8)
        action_lay.addLayout(self._action_buttons)
        self._detail_content_lay.addWidget(self._action_box)
        
        # Manage
        manage_box = QFrame()
        manage_box.setStyleSheet("QFrame { background: rgba(10, 12, 18, 220); border: 1px solid rgba(255,255,255,0.06); border-radius: 16px; }")
        manage_lay = QVBoxLayout(manage_box)
        manage_lay.setContentsMargins(16, 16, 16, 16)
        manage_title = QLabel("MANAGE")
        manage_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        manage_title.setStyleSheet("color: #ffffff; letter-spacing: 1px;")
        manage_lay.addWidget(manage_title)
        
        self._btn_rename = QPushButton("Rename Device")
        self._btn_disconnect = QPushButton("Disconnect")
        self._btn_reconnect = QPushButton("Reconnect")
        self._btn_forget = QPushButton("Forget Device")
        self._btn_forget.setStyleSheet("QPushButton { color: #ff5555; } QPushButton:hover { background: rgba(255, 85, 85, 0.1); border-color: #ff5555; }")
        
        manage_grid = QGridLayout()
        manage_grid.setSpacing(8)
        for i, b in enumerate([self._btn_rename, self._btn_disconnect, self._btn_reconnect, self._btn_forget]):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(34)
            if b != self._btn_forget:
                b.setStyleSheet("""
                    QPushButton { background: rgba(255,255,255,0.03); color: #ffffff; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; }
                    QPushButton:hover { background: rgba(0, 229, 255,0.08); border: 1px solid rgba(0, 229, 255,0.22); color: #00e5ff; }
                """)
            else:
                b.setStyleSheet("""
                    QPushButton { background: rgba(255,255,255,0.03); color: #ff5555; border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; }
                    QPushButton:hover { background: rgba(255,85,85,0.1); border: 1px solid rgba(255,85,85,0.3); }
                """)
            manage_grid.addWidget(b, i // 2, i % 2)
            
        manage_lay.addLayout(manage_grid)
        self._detail_content_lay.addWidget(manage_box)
        self._detail_content_lay.addStretch(1)
        self._detail_scroll.setWidget(self._detail_content)
        det_lay.addWidget(self._detail_scroll)
        
        self._main_stack.addWidget(self._detail_page)
        
        # ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        # PAGE 2 ΓÇö ADD DEVICE (QR WIZARD)
        # ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        self._add_device_page = QWidget()
        add_lay = QVBoxLayout(self._add_device_page)
        add_lay.setContentsMargins(0, 10, 0, 0)
        
        add_back_btn = QPushButton("ΓåÉ Cancel")
        add_back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_back_btn.setFixedSize(140, 32)
        add_back_btn.setFont(QFont("Segoe UI", 9))
        add_back_btn.setStyleSheet("""
            QPushButton { background: transparent; color: rgba(255,255,255,0.7); border: none; text-align: left; }
            QPushButton:hover { color: #00e5ff; }
        """)
        add_back_btn.clicked.connect(self._close_detail)
        add_lay.addWidget(add_back_btn)
        
        # Re-use the stacked widget logic for the wizard steps
        self._wizard_stack = QStackedWidget()
        
        # Wizard State 0: Select Platform
        w0 = QWidget()
        w0_lay = QVBoxLayout(w0)
        w0_lay.addStretch(1)
        w0_title = QLabel("SELECT DEVICE TYPE")
        w0_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        w0_title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        w0_title.setStyleSheet("color: #ffffff; letter-spacing: 2px;")
        w0_lay.addWidget(w0_title)
        w0_lay.addSpacing(20)
        
        w0_row = QHBoxLayout()
        w0_row.addStretch(1)
        self._cs_android_btn = QPushButton("ANDROID")
        self._cs_android_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cs_android_btn.setFixedSize(140, 80)
        self._cs_android_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._cs_android_btn.setStyleSheet("""
            QPushButton { background: rgba(10, 12, 18, 220); color: rgba(255,255,255,0.9); border: 1px solid rgba(255,255,255,0.12); border-radius: 12px; }
            QPushButton:hover { color: #00e5ff; border: 1px solid rgba(0, 229, 255,0.6); background: rgba(0, 229, 255,0.1); }
        """)
        self._cs_android_btn.clicked.connect(self._cinema_select_android)
        w0_row.addWidget(self._cs_android_btn)
        
        self._cs_pc_btn = QPushButton("PC\n(Coming Soon)")
        self._cs_pc_btn.setFixedSize(140, 80)
        self._cs_pc_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._cs_pc_btn.setEnabled(False)
        self._cs_pc_btn.setStyleSheet("QPushButton { background: rgba(10, 12, 18, 120); color: rgba(255,255,255,0.2); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; }")
        w0_row.addWidget(self._cs_pc_btn)
        w0_row.addStretch(1)
        w0_lay.addLayout(w0_row)
        w0_lay.addStretch(1)
        self._wizard_stack.addWidget(w0)
        
        # Wizard State 1: QR Code
        w1 = QWidget()
        w1_lay = QVBoxLayout(w1)
        w1_lay.addStretch(1)
        w1_title = QLabel("SCAN TO CONNECT")
        w1_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        w1_title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        w1_title.setStyleSheet("color: #ffffff; letter-spacing: 2px;")
        w1_lay.addWidget(w1_title)
        w1_lay.addSpacing(16)
        
        w1_qr_row = QHBoxLayout()
        w1_qr_row.addStretch(1)
        self._onb_qr_label = QLabel()
        self._onb_qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._onb_qr_label.setFixedSize(200, 200)
        self._onb_qr_label.setStyleSheet("background: #ffffff; border-radius: 6px; padding: 4px;")
        w1_qr_row.addWidget(self._onb_qr_label)
        w1_qr_row.addStretch(1)
        w1_lay.addLayout(w1_qr_row)
        
        w1_lay.addSpacing(16)
        self._onb_code_lbl = QLabel("------")
        self._onb_code_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._onb_code_lbl.setFont(QFont("Consolas", 18, QFont.Weight.Black))
        self._onb_code_lbl.setStyleSheet("color: #00e5ff; letter-spacing: 6px;")
        w1_lay.addWidget(self._onb_code_lbl)
        
        self._onb_status_lbl = QLabel("WAITING FOR CONNECTION")
        self._onb_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._onb_status_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._onb_status_lbl.setStyleSheet("color: rgba(255,255,255,0.5); letter-spacing: 1px;")
        w1_lay.addWidget(self._onb_status_lbl)
        
        w1_ctrl = QHBoxLayout()
        w1_ctrl.addStretch(1)
        w1_ref = QPushButton("Refresh Code")
        w1_ref.setCursor(Qt.CursorShape.PointingHandCursor)
        w1_ref.setFixedSize(120, 32)
        w1_ref.setStyleSheet("""
            QPushButton { background: rgba(255,255,255,0.05); color: #ffffff; border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; }
            QPushButton:hover { background: rgba(255,255,255,0.1); }
        """)
        w1_ref.clicked.connect(self._refresh_onboarding_offer)
        w1_ctrl.addWidget(w1_ref)
        w1_ctrl.addStretch(1)
        w1_lay.addLayout(w1_ctrl)
        w1_lay.addStretch(1)
        self._wizard_stack.addWidget(w1)
        
        # Wizard State 2: Success
        w2 = QWidget()
        w2_lay = QVBoxLayout(w2)
        w2_lay.addStretch(1)
        self._onb_success_title = QLabel("DEVICE CONNECTED")
        self._onb_success_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._onb_success_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Black))
        self._onb_success_title.setStyleSheet("color: #35ff75; letter-spacing: 2px;")
        w2_lay.addWidget(self._onb_success_title)
        self._onb_success_name = QLabel("")
        self._onb_success_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._onb_success_name.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        self._onb_success_name.setStyleSheet("color: #ffffff;")
        w2_lay.addWidget(self._onb_success_name)
        w2_lay.addStretch(1)
        self._wizard_stack.addWidget(w2)
        
        add_lay.addWidget(self._wizard_stack)
        self._main_stack.addWidget(self._add_device_page)
        
        root.addWidget(self._main_stack)
        
        self._onboarding_timer = QTimer(self)
        self._onboarding_timer.timeout.connect(self._tick_onboarding)
        self._onboarding_offer = {}
        self._onboarding_pulse = 0

    def _service_obj(self):
        return self._service or getattr(self.parentWidget(), "_brahma_connect", None)

    def set_service(self, service):
        self._service = service

    def _trigger_add_device(self):
        self._main_stack.setCurrentIndex(2)
        self._wizard_stack.setCurrentIndex(0)

    def _cinema_select_android(self):
        self._wizard_stack.setCurrentIndex(1)
        service = self._service_obj()
        self._onboarding_known_device_ids = set(str(d.get("device_id", "")) for d in service.list_devices()) if service else set()
        self._onboarding_timer.start(1000)
        self._refresh_onboarding_offer()

    def _refresh_onboarding_offer(self):
        service = self._service_obj()
        if service is None:
            self._onboarding_offer = {}
            self._onb_code_lbl.setText("------")
            self._onb_status_lbl.setText("GATEWAY UNAVAILABLE")
            return
        try:
            import io
            import qrcode

            self._onboarding_offer = dict(service.create_pairing_offer(device_name="Brahma Connect", platform="gateway"))
            code = str(self._onboarding_offer.get("pairing_code") or "------")
            self._onb_code_lbl.setText(code)
            self._onb_status_lbl.setText("WAITING FOR CONNECTION")
            
            payload = self._onboarding_offer
            text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if qrcode is not None:
                qr = qrcode.QRCode(box_size=6, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
                qr.add_data(text)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                pix = QPixmap()
                pix.loadFromData(buf.getvalue(), "PNG")
                self._onb_qr_label.setPixmap(
                    pix.scaled(
                        self._onb_qr_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                self._onb_qr_label.setText(text)
                
            self._tick_onboarding()
        except Exception as exc:
            self._onboarding_offer = {}
            self._onb_code_lbl.setText("------")
            self._onb_status_lbl.setText(f"ERROR: {exc}")

    def _tick_onboarding(self):
        if self._wizard_stack.currentIndex() != 1:
            return
        
        expires = self._onboarding_offer.get("expires")
        try:
            remaining = max(0, int(expires))
            if remaining <= 0 and self._onboarding_offer:
                self._onb_status_lbl.setText("CODE EXPIRED. REFRESH TO CONTINUE.")
        except Exception:
            pass
            
        service = self._service_obj()
        if service and self._onboarding_offer:
            devices = service.list_devices()
            new_device = next(
                (d for d in devices if str(d.get("device_id", "")) not in self._onboarding_known_device_ids),
                None,
            )
            if new_device:
                self._onboarding_timer.stop()
                self._connected_device = new_device
                self._trigger_onboarding_success()

    def _trigger_onboarding_success(self):
        self._wizard_stack.setCurrentIndex(2)
        dev = self._connected_device
        self._onb_success_name.setText(str(dev.get("name", "Device")).upper())
        QTimer.singleShot(2000, lambda: self.refresh(force=True))

    def _cancel_onboarding(self):
        self._onboarding_timer.stop()
        self._close_detail()

    def _clear_layout(self, layout):
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _on_device_selected(self, device_id: str):
        if self._selected_device_id == device_id:
            self._selected_device_id = None
        else:
            self._selected_device_id = device_id
        self.refresh(force=True)

    def _on_card_action_requested(self, device_id: str, action: str, payload: dict):
        if action == "rename":
            service = self._service_obj()
            if not service:
                return
            device = next((d for d in service.list_devices() if str(d.get("device_id", "")) == device_id), None)
            if device:
                self._rename_device(device_id, str(device.get("name", "")))
            return
        if action == "disconnect":
            self._apply_tile_action(device_id, "disconnect", payload)
            return
        if action == "forget":
            self._apply_tile_action(device_id, "forget", payload)
            return
        self._apply_tile_action(device_id, action, payload)
        return

    def _close_detail(self):
        self._selected_device_id = None
        self._main_stack.setCurrentIndex(0)
        self.refresh(force=True)

    def refresh(self, force: bool = False):
        service = self._service_obj()
        if service is None:
            return
            
        devices = service.list_devices()
        port = getattr(service, "port", 8765)
        self._gateway_meta.setText(f"Port: {port}  ┬╖  Devices: {len(devices)}")
        
        # If we are in Add Device or Detail page, keep it there
        if self._main_stack.currentIndex() not in (0,) and not force:
            pass # Keep current index unless forced to reset
        elif len(devices) == 0:
            self._main_stack.setCurrentIndex(0)
            self._overview_stack.setCurrentIndex(1) # Empty state
        else:
            self._main_stack.setCurrentIndex(0)
            self._overview_stack.setCurrentIndex(0) # Grid state
            
        # Rebuild grid
        self._clear_layout(self._grid)
        self._cards.clear()
        
        if devices:
            cols = 4
            for i, dev in enumerate(devices):
                card = _ConnectDeviceCard(dev)
                card.clicked.connect(self._on_device_selected)
                card.action_requested.connect(self._on_card_action_requested)
                card.set_expanded(str(dev.get("device_id", "")) == self._selected_device_id)
                self._cards.append(card)
                self._grid.addWidget(card, i // cols, i % cols)
            self._grid.setRowStretch(len(devices) // cols + 1, 1)

    def _apply_tile_action(self, device_id: str, action: str, payload: dict):
        service = self._service_obj()
        if not service:
            return
        try:
            if action == "rename":
                device = next((d for d in service.list_devices() if str(d.get("device_id", "")) == device_id), None)
                if device:
                    self._rename_device(device_id, str(device.get("name", "")))
                    return
            elif action == "disconnect":
                if hasattr(service, "disconnect_device"):
                    try:
                        asyncio.run(service.disconnect_device(device_id))
                    except Exception:
                        pass
            elif action == "forget":
                self._forget_device(device_id)
                return
            elif action == "set_pin":
                self._set_device_pin(device_id)
                return
            else:
                if hasattr(service, "route_command"):
                    service.route_command(device_id, action, payload)
                else:
                    service.execute_device_action(device_id, action, payload)
        except Exception:
            pass
        QTimer.singleShot(200, lambda: self.refresh(force=True))

    def _rename_device(self, device_id: str, current_name: str):
        new_name, ok = QInputDialog.getText(self, "Rename Device", "New name:", text=current_name)
        if ok and new_name.strip():
            service = self._service_obj()
            if service:
                try:
                    service.rename_device(device_id, new_name.strip())
                    self._on_device_selected(device_id)
                except Exception:
                    pass
            self.refresh(force=True)

    def _set_device_pin(self, device_id: str):
        pin, ok = QInputDialog.getText(self, "Set Device PIN", "Enter device PIN:", QLineEdit.EchoMode.Password)
        if ok and pin.strip():
            try:
                settings = {}
                if APP_SETTINGS_FILE.exists():
                    with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                
                device_pins = settings.get("device_pins", {})
                device_pins[device_id] = pin.strip()
                settings["device_pins"] = device_pins
                
                with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
                    json.dump(settings, f, indent=4)
            except Exception as e:
                print(f"Error saving pin: {e}")

    def _forget_device(self, device_id: str):
        service = self._service_obj()
        if service:
            try:
                if hasattr(service, "forget_device"):
                    service.forget_device(device_id)
                elif hasattr(service, "gateway") and hasattr(service.gateway, "device_manager"):
                    service.gateway.device_manager.remove(device_id)
            except Exception:
                pass
        self._close_detail()
        self.refresh(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh(force=True)
