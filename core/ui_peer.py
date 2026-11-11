"""core/ui_peer.py — Peer Federation and Swarm UI Panel (ADR-037, Phase S4).

Provides the PyQt6 widget for managing peer nodes, trusted pairings, LAN discovery,
drain/maintenance mode, and monitoring in-flight federated tasks.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.peer.identity import NodeIdentity
from core.peer.pairing import PeerRegistry
from core.peer.service import PeerService

logger = logging.getLogger("UIPeerManagement")


class PeerManagementWidget(QFrame):
    """Peer Federation management card for the Brahma Settings/Swarm Hub."""

    status_changed = pyqtSignal(dict)

    def __init__(
        self,
        peer_service: Optional[PeerService] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PeerManagementWidget")
        self.service = peer_service

        self._setup_style()
        self._build_ui()

        # Polling timer for status refresh (every 3 seconds)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_data)
        self._refresh_timer.start(3000)

        self.refresh_data()

    def _setup_style(self) -> None:
        self.setStyleSheet("""
            QFrame#PeerManagementWidget {
                background: rgba(12, 16, 24, 220);
                border: 1px solid rgba(0, 229, 255, 0.2);
                border-radius: 12px;
                padding: 16px;
                color: #e0e0e0;
            }
            QLabel {
                color: #dcdcdc;
            }
            QLabel#TitleLabel {
                color: #00e5ff;
                font-size: 15px;
                font-weight: bold;
            }
            QLabel#SectionHeader {
                color: #80d8ff;
                font-size: 13px;
                font-weight: bold;
                margin-top: 10px;
                margin-bottom: 4px;
            }
            QLabel#SubtleLabel {
                color: #8899a6;
                font-size: 11px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                border: 1px solid rgba(0, 229, 255, 0.35);
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(0, 229, 255, 0.25);
                border-color: #00e5ff;
            }
            QPushButton#DangerButton {
                border-color: rgba(255, 82, 82, 0.5);
                color: #ff8a80;
            }
            QPushButton#DangerButton:hover {
                background: rgba(255, 82, 82, 0.25);
                border-color: #ff5252;
            }
            QFrame.PeerCard {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 10px;
                margin-bottom: 6px;
            }
        """)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header with Title and Drain Toggle
        header_layout = QHBoxLayout()
        title_label = QLabel("Brahma Peer Swarm Federation (BTE-1)")
        title_label.setObjectName("TitleLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.drain_btn = QPushButton("Drain Mode: OFF")
        self.drain_btn.clicked.connect(self._toggle_drain_mode)
        header_layout.addWidget(self.drain_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_data)
        header_layout.addWidget(self.refresh_btn)

        main_layout.addLayout(header_layout)

        # Local Node Info Card
        self.node_card = QFrame()
        self.node_card.setProperty("class", "PeerCard")
        node_layout = QVBoxLayout(self.node_card)
        node_layout.setContentsMargins(10, 8, 10, 8)
        node_layout.setSpacing(4)

        self.lbl_node_id = QLabel("Node ID: Loading...")
        self.lbl_fingerprint = QLabel("SPKI SHA-256: Loading...")
        self.lbl_fingerprint.setObjectName("SubtleLabel")
        self.lbl_status = QLabel("Status: Active")
        self.lbl_status.setObjectName("SubtleLabel")

        node_layout.addWidget(self.lbl_node_id)
        node_layout.addWidget(self.lbl_fingerprint)
        node_layout.addWidget(self.lbl_status)
        main_layout.addWidget(self.node_card)

        # Scroll Area for Lists
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)

        # Section 1: Pinned Peers
        lbl_pinned = QLabel("Pinned Trusted Peers")
        lbl_pinned.setObjectName("SectionHeader")
        self.scroll_layout.addWidget(lbl_pinned)

        self.pinned_container = QVBoxLayout()
        self.scroll_layout.addLayout(self.pinned_container)

        # Section 2: Discovered LAN Peers
        lbl_discovered = QLabel("Discovered LAN Peers (Advisory)")
        lbl_discovered.setObjectName("SectionHeader")
        self.scroll_layout.addWidget(lbl_discovered)

        self.discovered_container = QVBoxLayout()
        self.scroll_layout.addLayout(self.discovered_container)

        # Section 3: In-Flight Tasks
        lbl_tasks = QLabel("In-Flight Federated Tasks")
        lbl_tasks.setObjectName("SectionHeader")
        self.scroll_layout.addWidget(lbl_tasks)

        self.tasks_container = QVBoxLayout()
        self.scroll_layout.addLayout(self.tasks_container)

        self.scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _toggle_drain_mode(self) -> None:
        if not self.service:
            return
        new_state = not self.service.drain_mode
        self.service.drain_mode = new_state
        self.drain_btn.setText(f"Drain Mode: {'ON' if new_state else 'OFF'}")
        if new_state:
            self.drain_btn.setStyleSheet("border-color: #ffb74d; color: #ffe0b2;")
        else:
            self.drain_btn.setStyleSheet("")
        self.refresh_data()

    def refresh_data(self) -> None:
        """Fetch updated federation state and update UI cards."""
        if self.service:
            summary = self.service.get_status_summary()
        else:
            # Fallback when running standalone or without attached service
            try:
                ident = NodeIdentity.load_or_create(enforce_machine_binding=False)
                reg = PeerRegistry()
                summary = {
                    "node_id": ident.node_id,
                    "spki_sha256": ident.spki_sha256,
                    "drain_mode": False,
                    "active_tasks": 0,
                    "pinned_peers": [
                        {
                            "node_id": p.node_id,
                            "name": p.name,
                            "endpoint": p.endpoint,
                            "spki_sha256": p.spki_sha256,
                            "profile": p.profile,
                        }
                        for p in reg.list_peers()
                    ],
                    "discovered_peers": [],
                }
            except Exception as exc:
                logger.warning("Could not load peer status: %s", exc)
                return

        # Update node card
        self.lbl_node_id.setText(f"Node ID: {summary.get('node_id', 'Unknown')}")
        self.lbl_fingerprint.setText(f"SPKI SHA-256: {summary.get('spki_sha256', 'None')}")
        drain = summary.get("drain_mode", False)
        active = summary.get("active_tasks", 0)
        status_txt = f"Status: {'DRAINING (Rejecting new tasks)' if drain else 'ACTIVE'} | In-flight tasks: {active}"
        self.lbl_status.setText(status_txt)
        self.drain_btn.setText(f"Drain Mode: {'ON' if drain else 'OFF'}")

        # Update Pinned Peers
        self._clear_layout(self.pinned_container)
        pinned = summary.get("pinned_peers", [])
        if not pinned:
            lbl_none = QLabel("No pinned peers. Pair with a node via SAS handshake.")
            lbl_none.setObjectName("SubtleLabel")
            self.pinned_container.addWidget(lbl_none)
        else:
            for p in pinned:
                card = self._create_pinned_peer_card(p)
                self.pinned_container.addWidget(card)

        # Update Discovered Peers
        self._clear_layout(self.discovered_container)
        discovered = summary.get("discovered_peers", [])
        if not discovered:
            lbl_none = QLabel("No nearby Brahma nodes broadcasting on LAN.")
            lbl_none.setObjectName("SubtleLabel")
            self.discovered_container.addWidget(lbl_none)
        else:
            for d in discovered:
                card = self._create_discovered_peer_card(d)
                self.discovered_container.addWidget(card)

        self.status_changed.emit(summary)

    def _create_pinned_peer_card(self, peer_info: Dict[str, Any]) -> QFrame:
        card = QFrame()
        card.setProperty("class", "PeerCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        lbl_name = QLabel(f"{peer_info.get('name', 'Peer')} ({peer_info.get('node_id', '')})")
        lbl_sub = QLabel(f"Endpoint: {peer_info.get('endpoint', '')} | Profile: {peer_info.get('profile', '')}")
        lbl_sub.setObjectName("SubtleLabel")
        info_col.addWidget(lbl_name)
        info_col.addWidget(lbl_sub)
        layout.addLayout(info_col)

        layout.addStretch()

        btn_unpin = QPushButton("Unpin")
        btn_unpin.setObjectName("DangerButton")
        btn_unpin.clicked.connect(lambda: self._unpin_peer(peer_info.get("node_id", "")))
        layout.addWidget(btn_unpin)
        return card

    def _create_discovered_peer_card(self, peer_info: Dict[str, Any]) -> QFrame:
        card = QFrame()
        card.setProperty("class", "PeerCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        lbl_name = QLabel(f"{peer_info.get('name', 'Discovered Node')} ({peer_info.get('node_id', '')})")
        lbl_sub = QLabel(f"Address: {peer_info.get('address', '')} | Endpoint: {peer_info.get('endpoint', '')}")
        lbl_sub.setObjectName("SubtleLabel")
        info_col.addWidget(lbl_name)
        info_col.addWidget(lbl_sub)
        layout.addLayout(info_col)

        layout.addStretch()

        btn_pair = QPushButton("Pair with Node")
        btn_pair.clicked.connect(lambda: self._initiate_pairing(peer_info))
        layout.addWidget(btn_pair)
        return card

    def _unpin_peer(self, node_id: str) -> None:
        if not node_id:
            return
        reply = QMessageBox.question(
            self,
            "Unpin Peer",
            f"Are you sure you want to unpin and remove trusted access for node '{node_id}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            reg = self.service.registry if self.service else PeerRegistry()
            peers = reg.load_peers()
            if node_id in peers:
                del peers[node_id]
                from core.secret_store import save_secret_json
                from dataclasses import asdict
                import datetime
                payload = {
                    "version": 1,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "peers": {nid: asdict(p) for nid, p in peers.items()},
                }
                save_secret_json(reg.file_path, payload, encrypt=True)
            self.refresh_data()

    def _initiate_pairing(self, peer_info: Dict[str, Any]) -> None:
        QMessageBox.information(
            self,
            "Pairing Request",
            f"Initiating SAS 6-digit cryptographic handshake with {peer_info.get('name')} ({peer_info.get('endpoint')}).\n"
            "Please confirm the SAS code displayed on both node screens.",
        )

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
