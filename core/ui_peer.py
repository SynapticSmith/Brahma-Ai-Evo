"""core/ui_peer.py — Peer Federation and Swarm UI Panel (ADR-037, Phase S4).

Provides the PyQt6 widget for managing peer nodes, trusted pairings, LAN discovery,
drain/maintenance mode, and monitoring in-flight federated tasks.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
from typing import Any, Dict, Optional
import urllib.request

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.peer.identity import NodeIdentity
from core.peer.model import now_iso
from core.peer.pairing import PairingCoordinator, PeerRegistry, PinnedPeer
from core.peer.service import PeerService
from core.peer.store import PeerTaskStore

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
        if peer_service is None:
            try:
                from core.peer.service import get_active_peer_service

                peer_service = get_active_peer_service()
            except Exception:
                peer_service = None
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

        self.discovery_btn = QPushButton("LAN Discovery: OFF")
        self.discovery_btn.clicked.connect(self._toggle_discovery)
        header_layout.addWidget(self.discovery_btn)

        self.drain_btn = QPushButton("Drain Mode: OFF")
        self.drain_btn.clicked.connect(self._toggle_drain_mode)
        header_layout.addWidget(self.drain_btn)

        self.offer_btn = QPushButton("Create Offer")
        self.offer_btn.clicked.connect(self._create_pairing_offer_dialog)
        header_layout.addWidget(self.offer_btn)

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

    def _toggle_discovery(self) -> None:
        """Start or stop the UDP beacon the pairing flow depends on.

        Pairing is manual either way: one node creates an offer, the other
        pastes it into a discovered card. But the initiator can only click
        'Pair with Node' on a *discovered* peer, and the GUI starts brahma_api
        without the CLI's ``--enable-peer-discovery`` flag. Without this toggle
        the shipped card could never pair, so it lives here rather than in the
        serve path: broadcasting presence on the LAN stays an explicit click.
        """
        if not self.service:
            return
        try:
            if getattr(self.service, "listener", None) is None:
                self.service.start_discovery()
            else:
                self.service.stop_discovery()
        except Exception as exc:
            logger.warning("Could not toggle LAN discovery: %s", exc)
            QMessageBox.warning(
                self,
                "LAN Discovery",
                f"Could not start LAN discovery: {exc}\n"
                "Another process may already hold UDP 8444.",
            )
        self.refresh_data()

    def _sync_discovery_button(self) -> None:
        """Reflect the listener's actual state, not the last click."""
        on = bool(self.service and getattr(self.service, "listener", None) is not None)
        self.discovery_btn.setText(f"LAN Discovery: {'ON' if on else 'OFF'}")
        self.discovery_btn.setStyleSheet(
            "border-color: #4fc3f7; color: #b3e5fc;" if on else ""
        )

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
        self._sync_discovery_button()

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

        # Update In-Flight Tasks
        self._clear_layout(self.tasks_container)
        store = self.service.store if self.service else PeerTaskStore()
        active_tasks = store.list_active_tasks(limit=20)
        if not active_tasks:
            lbl_none_tasks = QLabel("No active or in-flight federated tasks.")
            lbl_none_tasks.setObjectName("SubtleLabel")
            self.tasks_container.addWidget(lbl_none_tasks)
        else:
            for t in active_tasks:
                card = self._create_task_card(t)
                self.tasks_container.addWidget(card)

        self.status_changed.emit(summary)

    def _create_task_card(self, task: Dict[str, Any]) -> QFrame:
        card = QFrame()
        card.setProperty("class", "PeerCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        lbl_id = QLabel(f"Task: {task.get('task_id', '')} | Origin: {task.get('origin_node', '')}")
        lbl_meta = QLabel(f"State: {str(task.get('state', '')).upper()} | Accepted: {task.get('accepted_at', '')}")
        lbl_meta.setObjectName("SubtleLabel")
        info_col.addWidget(lbl_id)
        info_col.addWidget(lbl_meta)
        layout.addLayout(info_col)

        layout.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("DangerButton")
        btn_cancel.clicked.connect(lambda: self._cancel_task(task.get("origin_node", ""), task.get("task_id", "")))
        layout.addWidget(btn_cancel)
        return card

    def _cancel_task(self, origin_node: str, task_id: str) -> None:
        if not task_id:
            return
        store = self.service.store if self.service else PeerTaskStore()
        store.request_cancel(origin_node, task_id)
        self.refresh_data()

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

    def _create_pairing_offer_dialog(self) -> None:
        """Generate a fresh single-use 120s pairing offer and show dialog."""
        ident = self.service.identity if self.service else NodeIdentity.load_or_create(enforce_machine_binding=False)
        reg = self.service.registry if self.service else PeerRegistry()
        coord = getattr(self.service, "coordinator", None) or PairingCoordinator(ident, reg)
        host = "127.0.0.1"
        port = 8443
        if self.service and self.service.endpoint:
            ep = self.service.endpoint.split("//")[-1]
            if ":" in ep:
                try:
                    host = ep.split(":")[0]
                    port = int(ep.split(":")[1].split("/")[0])
                except Exception:
                    pass
        from core.peer.endpoint import announced_host as _resolve_host
        from core.peer.endpoint import is_wildcard_host

        if is_wildcard_host(host):
            host = _resolve_host(host)
        offer = coord.create_offer(host, port)
        if self.service:
            self.service.pending_offers[offer.nonce] = offer
        uri = offer.to_uri()
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Pairing Offer Created")
        dlg.setText(f"Single-use 120s Pairing Offer URI:\n\n{uri}\n\nShare this URI with the remote node to pair.")
        dlg.exec()

    def _initiate_pairing(self, peer_info: Dict[str, Any]) -> None:
        """Initiate mutual SAS cryptographic pairing with a discovered peer."""
        ident = self.service.identity if self.service else NodeIdentity.load_or_create(enforce_machine_binding=False)
        reg = self.service.registry if self.service else PeerRegistry()
        coord = getattr(self.service, "coordinator", None) or PairingCoordinator(ident, reg)

        endpoint = (peer_info.get("endpoint") or "").rstrip("/")
        if not endpoint:
            endpoint = f"https://{peer_info.get('address', '127.0.0.1')}:8443"

        # Ask operator for the 120s pairing offer URI generated by target peer
        offer_uri_input, ok = QInputDialog.getText(
            self,
            "Peer SAS Pairing",
            f"Enter the 120s Pairing Offer URI generated on {peer_info.get('name', 'Peer')}:\n"
            "(Format: brahma://pair?v=1&node=...)",
        )
        if not ok or not offer_uri_input.strip():
            return

        try:
            from core.peer.pairing import PairingOffer

            offer = PairingOffer.from_uri(offer_uri_input.strip())
        except Exception as exc:
            QMessageBox.critical(self, "Invalid Offer URI", f"Failed to parse pairing offer URI: {exc}")
            return

        try:
            eph_priv, eph_pub, static_pub = coord.begin_exchange(offer, is_initiator=True)
        except Exception as exc:
            QMessageBox.warning(self, "Pairing Error", f"Failed to begin pairing exchange: {exc}")
            return

        exchange_url = f"{endpoint}/pair/exchange"
        payload = {
            "offer_uri": offer.to_uri(),
            "initiator_node_id": ident.node_id,
            "initiator_spki_sha256": ident.spki_sha256,
            "initiator_name": ident.hostname,
            "initiator_endpoint": self.service.endpoint if self.service else f"https://{ident.hostname}:8443",
            "initiator_eph_pub": base64.b64encode(eph_pub).decode("ascii"),
            "initiator_static_pub": base64.b64encode(static_pub).decode("ascii"),
            "profile": "worker",
            "workspaces": [],
            "labels": {"node_id": ident.node_id},
        }

        exchanged_data = None
        ctx = None
        if endpoint.startswith("https://"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            req = urllib.request.Request(
                exchange_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0, context=ctx) as resp:
                if resp.status == 200:
                    exchanged_data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Pairing Network Error",
                f"Could not connect to {exchange_url}: {exc}\n"
                "Verify the peer's Brahma port is reachable and the offer is active.",
            )
            return

        if not exchanged_data:
            QMessageBox.warning(self, "Pairing Failed", "Target node returned an empty response.")
            return

        try:
            peer_eph_bytes = base64.b64decode(exchanged_data["callee_eph_pub"])
            peer_static_bytes = base64.b64decode(exchanged_data["callee_static_pub"])
            sas_code, session_key = coord.complete_key_exchange(
                eph_priv=eph_priv,
                peer_node_id=exchanged_data["callee_node_id"],
                peer_eph_pub_bytes=peer_eph_bytes,
                peer_static_pub_bytes=peer_static_bytes,
                peer_spki_sha256=exchanged_data["callee_spki_sha256"],
                nonce=offer.nonce,
                is_initiator=True,
            )
            if sas_code != exchanged_data.get("sas_code"):
                QMessageBox.critical(
                    self,
                    "SAS Mismatch Warning",
                    "Security tripwire: SAS code does not match peer response! Potential interception.",
                )
                return

            sas_confirm_msg = (
                f"Cryptographic SAS Code: {sas_code}\n\n"
                f"Target Node: {peer_info.get('name', 'Peer')} ({exchanged_data['callee_node_id']})\n\n"
                "Confirm that this 6-digit SAS code matches the code on the remote machine."
            )
            reply = QMessageBox.question(
                self,
                "Confirm Peer SAS Handshake",
                sas_confirm_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                unwrapped = coord.unwrap_token_payload(
                    session_key,
                    base64.b64decode(exchanged_data["encrypted_token"]),
                )
                peer_record = PinnedPeer(
                    node_id=exchanged_data["callee_node_id"],
                    name=peer_info.get("name", "Remote Peer"),
                    endpoint=endpoint,
                    spki_sha256=exchanged_data["callee_spki_sha256"],
                    token=unwrapped["token"],
                    profile=unwrapped.get("profile", "worker"),
                    paired_at=now_iso(),
                    static_pub_raw=exchanged_data["callee_static_pub"],
                    workspaces=list(exchanged_data.get("workspaces") or []),
                    labels=dict(exchanged_data.get("labels") or {}),
                )
                reg.save_peer(peer_record, allow_re_pair=True)
                QMessageBox.information(
                    self,
                    "Pairing Complete",
                    f"Successfully verified and pinned {peer_record.name} ({peer_record.node_id})!",
                )
                self.refresh_data()
        except Exception as exc:
            QMessageBox.warning(self, "Pairing Error", f"Failed to complete SAS handshake: {exc}")

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()


def build_peer_management_card(
    parent: Optional[QWidget] = None,
    service: Optional[PeerService] = None,
) -> QWidget:
    """Entry point for embedding in SystemConnectivityPage."""
    if service is None:
        try:
            from core.peer.service import get_active_peer_service

            service = get_active_peer_service()
        except Exception:
            service = None
    return PeerManagementWidget(peer_service=service, parent=parent)
