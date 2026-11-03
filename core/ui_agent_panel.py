"""
core/ui_agent_panel.py — Agent & Orchestrator Bridge Settings Card (ADR-018, Q2).

Leaf UI module exposing discovered local model servers, CLI agents, and orchestrators
in the Settings Hub. Kept in a dedicated module to preserve ui.py and ui_connectivity.py
line ratchets.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.agent_bridge.bridge import get_agent_bridge
from core.agent_bridge.models import AgentRecord, ProbeResult

logger = logging.getLogger("UIAgentPanel")


class _AsyncBridgeWorker(QThread):
    """Background worker for non-blocking discovery and health probes."""

    finished_scan = pyqtSignal(list)
    finished_probe = pyqtSignal(str, object)

    def __init__(self, action: str, agent_id: Optional[str] = None):
        super().__init__()
        self.action = action
        self.agent_id = agent_id

    def run(self):
        bridge = get_agent_bridge()
        if self.action == "scan":
            records = bridge.discover()
            self.finished_scan.emit(records)
        elif self.action == "probe" and self.agent_id:
            probe_res = bridge.probe(self.agent_id)
            self.finished_probe.emit(self.agent_id, probe_res)


class AgentBridgeCard(QFrame):
    """Settings Hub card displaying discovered agents and orchestrator services."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("AgentBridgeCard")
        self.setStyleSheet("""
            QFrame#AgentBridgeCard {
                background: rgba(10, 12, 18, 160);
                border: 1.5px solid rgba(0, 229, 255, 0.15);
                border-radius: 18px;
                padding: 16px;
            }
            QFrame#AgentBridgeCard:hover {
                border: 1.5px solid rgba(0, 229, 255, 0.35);
            }
        """)

        self._active_workers: List[_AsyncBridgeWorker] = []
        self._init_ui()
        self.refresh_records()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # Header row
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Agent & Orchestrator Bridge")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff;")
        sub = QLabel("Discovered local model runtimes, coding agents, and orchestrators.")
        sub.setFont(QFont("Segoe UI", 9))
        sub.setStyleSheet("color: #8892b0;")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        header_row.addLayout(title_box)

        header_row.addStretch()

        self._btn_scan = QPushButton("🔄 Scan Environment")
        self._btn_scan.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.1);
                color: #00e5ff;
                border: 1px solid rgba(0, 229, 255, 0.3);
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.2);
            }
        """)
        self._btn_scan.clicked.connect(self._start_scan)
        header_row.addWidget(self._btn_scan)
        lay.addLayout(header_row)

        # Status summary bar
        self._summary_lbl = QLabel("Checking bridge...")
        self._summary_lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        lay.addWidget(self._summary_lbl)

        # Container for agent items
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 4, 0, 4)
        self._list_layout.setSpacing(8)
        lay.addWidget(self._list_container)

    def refresh_records(self):
        """Populate list from the bridge registry."""
        bridge = get_agent_bridge()
        records = bridge.list_records()

        # Clear existing items
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if not records:
            empty = QLabel("No local agents or orchestrators detected yet. Click 'Scan Environment' to probe.")
            empty.setStyleSheet("color: #64748b; font-style: italic; padding: 10px 0;")
            self._list_layout.addWidget(empty)
            self._summary_lbl.setText("0 agents registered.")
            return

        active_count = sum(1 for r in records if r.enabled)
        self._summary_lbl.setText(f"{len(records)} agent(s) registered · {active_count} enabled.")

        for rec in records:
            row = self._create_agent_row(rec)
            self._list_layout.addWidget(row)

    def _create_agent_row(self, rec: AgentRecord) -> QWidget:
        row = QFrame()
        row.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
                padding: 8px 12px;
            }
            QFrame:hover {
                background: rgba(255, 255, 255, 0.05);
                border-color: rgba(0, 229, 255, 0.2);
            }
        """)
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(4, 4, 4, 4)
        rlay.setSpacing(10)

        # Kind icon
        icons = {
            "local_model": "🧠",
            "cli_agent": "💻",
            "mcp_server": "🔌",
            "orchestrator": "🌐",
            "peer": "🤝",
        }
        icon = icons.get(rec.kind, "🤖")
        icon_lbl = QLabel(icon)
        icon_lbl.setFont(QFont("Segoe UI Emoji", 14))
        rlay.addWidget(icon_lbl)

        # Info block
        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        name_lbl = QLabel(f"<b>{rec.display_name}</b> <span style='color: #64748b;'>({rec.kind})</span>")
        name_lbl.setTextFormat(Qt.TextFormat.RichText)
        name_lbl.setStyleSheet("color: #ffffff; font-size: 11px;")
        info_box.addWidget(name_lbl)

        target = rec.endpoint or rec.executable or "Local configuration"
        target_lbl = QLabel(target)
        target_lbl.setStyleSheet("color: #8892b0; font-size: 10px;")
        info_box.addWidget(target_lbl)
        rlay.addLayout(info_box, 1)

        # Status badge
        status_colors = {
            "connected": ("#00ffaa", "🟢 Connected"),
            "degraded": ("#ffaa00", "🟡 Degraded"),
            "found": ("#94a3b8", "⚪ Found"),
            "error": ("#ff5555", "🔴 Error"),
            "disabled": ("#64748b", "⏸️ Disabled"),
        }
        color, text = status_colors.get(rec.status, ("#94a3b8", rec.status.capitalize()))
        status_lbl = QLabel(text)
        status_lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: bold;")
        rlay.addWidget(status_lbl)

        # Health probe button
        btn_probe = QPushButton("Probe")
        btn_probe.setFixedWidth(60)
        btn_probe.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
                padding: 4px;
                font-size: 10px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.15);
                border-color: #00e5ff;
                color: #00e5ff;
            }
        """)
        btn_probe.clicked.connect(lambda _, a_id=rec.id, s_lbl=status_lbl, b=btn_probe: self._start_probe(a_id, s_lbl, b))
        rlay.addWidget(btn_probe)

        # Enable/Disable toggle checkbox
        chk = QCheckBox()
        chk.setChecked(rec.enabled)
        chk.setToolTip("Enable or disable this agent for tool calls")
        chk.stateChanged.connect(lambda state, a_id=rec.id: self._on_toggle_agent(a_id, state == 2))
        rlay.addWidget(chk)

        return row

    def _start_scan(self):
        self._btn_scan.setEnabled(False)
        self._btn_scan.setText("⏳ Scanning...")
        self._summary_lbl.setText("Scanning local environment for models and agents...")

        worker = _AsyncBridgeWorker(action="scan")

        def _on_scan_done(records):
            self._btn_scan.setEnabled(True)
            self._btn_scan.setText("🔄 Scan Environment")
            self.refresh_records()
            if worker in self._active_workers:
                self._active_workers.remove(worker)

        worker.finished_scan.connect(_on_scan_done)
        self._active_workers.append(worker)
        worker.start()

    def _start_probe(self, agent_id: str, status_lbl: QLabel, btn: QPushButton):
        btn.setEnabled(False)
        btn.setText("...")
        status_lbl.setText("Probing...")
        status_lbl.setStyleSheet("color: #00e5ff; font-size: 10px;")

        worker = _AsyncBridgeWorker(action="probe", agent_id=agent_id)

        def _on_probe_done(a_id, res: Optional[ProbeResult]):
            btn.setEnabled(True)
            btn.setText("Probe")
            if res and res.ok:
                status_lbl.setText("🟢 Online")
                status_lbl.setStyleSheet("color: #00ffaa; font-size: 10px; font-weight: bold;")
            elif res:
                status_lbl.setText(f"🟡 {res.stage}")
                status_lbl.setStyleSheet("color: #ffaa00; font-size: 10px; font-weight: bold;")
            else:
                status_lbl.setText("🔴 Offline")
                status_lbl.setStyleSheet("color: #ff5555; font-size: 10px; font-weight: bold;")
            if worker in self._active_workers:
                self._active_workers.remove(worker)

        worker.finished_probe.connect(_on_probe_done)
        self._active_workers.append(worker)
        worker.start()

    def _on_toggle_agent(self, agent_id: str, enabled: bool):
        bridge = get_agent_bridge()
        bridge.registry.set_enabled(agent_id, enabled)
        logger.info("Agent %s enabled set to %s", agent_id, enabled)
        # Update summary counter
        records = bridge.list_records()
        active_count = sum(1 for r in records if r.enabled)
        self._summary_lbl.setText(f"{len(records)} agent(s) registered · {active_count} enabled.")


def build_agent_bridge_card(parent: Optional[QWidget] = None) -> AgentBridgeCard:
    """Factory helper to build and return the AgentBridgeCard."""
    return AgentBridgeCard(parent)
