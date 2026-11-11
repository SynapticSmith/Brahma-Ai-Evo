"""Peer Service Coordinator and Health/Drain Lifecycle (ADR-037 & Deep Dive §8.4, §12).

Coordinates local node federation state, drain/maintenance mode, discovery,
and provides structured status summaries for UI and diagnostic reporting.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from core.peer.artifacts import PeerArtifactStore
from core.peer.discovery import DiscoveredPeer, PeerDiscoveryBeacon, PeerDiscoveryListener
from core.peer.identity import NodeIdentity
from core.peer.pairing import PeerRegistry
from core.peer.scheduler import ReservationLedger
from core.peer.store import PeerTaskStore

logger = logging.getLogger("brahma.peer.service")


class PeerService:
    """Manages the node's peer federation subsystems and operational lifecycle."""

    def __init__(
        self,
        *,
        identity: Optional[NodeIdentity] = None,
        store: Optional[PeerTaskStore] = None,
        task_store: Optional[PeerTaskStore] = None,
        registry: Optional[PeerRegistry] = None,
        artifact_store: Optional[PeerArtifactStore] = None,
        ledger: Optional[ReservationLedger] = None,
        endpoint: str = "https://127.0.0.1:8443",
        enable_discovery: bool = False,
    ) -> None:
        self.identity = identity or NodeIdentity.load_or_create(enforce_machine_binding=False)
        self.store = store or task_store or PeerTaskStore()
        self.registry = registry or PeerRegistry()
        self.artifact_store = artifact_store or PeerArtifactStore()
        self.ledger = ledger or ReservationLedger()
        self.endpoint = endpoint
        self._drain_mode = False
        self._lock = threading.Lock()

        self.beacon: Optional[PeerDiscoveryBeacon] = None
        self.listener: Optional[PeerDiscoveryListener] = None

        if enable_discovery:
            self.start_discovery()

    @property
    def drain_mode(self) -> bool:
        """When True, node finishes in-flight tasks but rejects new admissions."""
        with self._lock:
            return self._drain_mode

    @drain_mode.setter
    def drain_mode(self, value: bool) -> None:
        with self._lock:
            self._drain_mode = bool(value)
            logger.info("Peer service drain mode set to: %s", self._drain_mode)

    def start_discovery(self) -> None:
        """Start UDP broadcast beacon and discovery listener."""
        with self._lock:
            if self.beacon is None:
                self.beacon = PeerDiscoveryBeacon(
                    node_id=self.identity.node_id,
                    name=self.identity.hostname,
                    endpoint=self.endpoint,
                    spki_sha256=self.identity.spki_sha256,
                )
                self.beacon.start()

            if self.listener is None:
                self.listener = PeerDiscoveryListener()
                self.listener.start()

    def stop_discovery(self) -> None:
        """Stop UDP discovery beacon and listener."""
        with self._lock:
            if self.beacon:
                self.beacon.stop()
                self.beacon = None
            if self.listener:
                self.listener.stop()
                self.listener = None

    def close(self) -> None:
        """Shutdown all background discovery routines."""
        self.stop_discovery()

    def get_status_summary(self) -> Dict[str, Any]:
        """Generate structured status snapshot for HUD and diagnostics."""
        with self._lock:
            pinned = self.registry.list_peers()
            discovered: List[DiscoveredPeer] = self.listener.list_discovered() if self.listener else []

            # Check in-flight / active tasks
            active_count = self.ledger.total_active_tasks()

            return {
                "node_id": self.identity.node_id,
                "hostname": self.identity.hostname,
                "spki_sha256": self.identity.spki_sha256,
                "endpoint": self.endpoint,
                "drain_mode": self._drain_mode,
                "active_tasks": active_count,
                "pinned_peers_count": len(pinned),
                "pinned_peers": [
                    {
                        "node_id": p.node_id,
                        "name": p.name,
                        "endpoint": p.endpoint,
                        "spki_sha256": p.spki_sha256,
                        "profile": p.profile,
                        "paired_at": p.paired_at,
                    }
                    for p in pinned
                ],
                "discovered_peers": [d.to_dict() for d in discovered],
            }
