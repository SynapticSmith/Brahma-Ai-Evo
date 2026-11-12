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
from core.peer.pairing import PeerRegistry, prune_pending_offers
from core.peer.scheduler import ReservationLedger
from core.peer.store import PeerTaskStore

logger = logging.getLogger("brahma.peer.service")

_active_peer_service_lock = threading.Lock()
_active_peer_service: Optional["PeerService"] = None


def get_active_peer_service() -> Optional["PeerService"]:
    """Retrieve the globally active PeerService instance for the process."""
    with _active_peer_service_lock:
        return _active_peer_service


def set_active_peer_service(service: Optional["PeerService"]) -> None:
    """Register or clear the globally active PeerService instance."""
    global _active_peer_service
    with _active_peer_service_lock:
        _active_peer_service = service


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
        endpoint: str = "http://127.0.0.1:8010",
        enable_discovery: bool = False,
    ) -> None:
        self.identity = identity or NodeIdentity.load_or_create(enforce_machine_binding=False)
        self.store = store or task_store or PeerTaskStore()
        self.registry = registry or PeerRegistry()
        self.artifact_store = artifact_store or PeerArtifactStore()
        self.ledger = ledger or ReservationLedger()
        from core.peer.endpoint import normalize_endpoint

        # A wildcard bind is reachable but not dialable; the beacon and the
        # card both read ``self.endpoint``, so normalise it once here.
        self.endpoint = normalize_endpoint(endpoint)
        self._drain_mode = False
        self._lock = threading.Lock()

        self.beacon: Optional[PeerDiscoveryBeacon] = None
        self.listener: Optional[PeerDiscoveryListener] = None

        from core.peer.pairing import PairingCoordinator

        self.coordinator = PairingCoordinator(self.identity, self.registry)
        self.pending_offers: Dict[str, Any] = {}

        if enable_discovery:
            self.start_discovery()

        global _active_peer_service
        if _active_peer_service is None:
            _active_peer_service = self

    def prune_pending_offers(self, max_age_s: float = 120.0) -> int:
        """Prune expired pending pairing offers to prevent unbounded accumulation."""
        with self._lock:
            return prune_pending_offers(self.pending_offers, max_age_s=max_age_s)

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
                self.listener = PeerDiscoveryListener(self_node_id=self.identity.node_id)
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

    def select_candidate_peer(
        self,
        *,
        required_profile: Optional[str] = None,
        required_workspace: Optional[str] = None,
        required_labels: Optional[Any] = None,
        required_verbs: Optional[Any] = None,
        preferred_node: Optional[str] = None,
        preferred_labels: Optional[Any] = None,
    ) -> Optional[Any]:
        """Select the best pinned peer matching execution criteria using S5 capability scoring."""
        from core.peer.capabilities import PeerCapabilityCard, select_best_peer

        with self._lock:
            pinned = self.registry.list_peers()
            if not pinned:
                return None
            cards = []
            peer_by_id = {}
            for p in pinned:
                peer_by_id[p.node_id] = p
                cards.append(
                    PeerCapabilityCard(
                        node_id=p.node_id,
                        name=p.name,
                        endpoint=p.endpoint,
                        spki_sha256=p.spki_sha256,
                        profiles=[p.profile] if p.profile else ["reader", "worker"],
                        workspaces=list(getattr(p, "workspaces", [])),
                        labels=(
                            set(p.labels.keys())
                            if isinstance(getattr(p, "labels", None), dict)
                            else set(getattr(p, "labels", []))
                        ),
                        active_tasks=self.ledger.active_tasks_for_node(p.node_id),
                    )
                )
            best_card = select_best_peer(
                cards,
                required_profile=required_profile,
                required_workspace=required_workspace,
                required_labels=required_labels,
                required_verbs=required_verbs,
                preferred_node=preferred_node,
                preferred_labels=preferred_labels,
            )
            return peer_by_id.get(best_card.node_id) if best_card else None

    def build_verification_request(
        self,
        original_task: Any,
        original_result: Any,
        verifier_node: str,
        *,
        coordinator_node: Optional[str] = None,
    ) -> Any:
        """Create an isolated, read-only verifier peer task request (Idea I-08)."""
        from core.peer.scheduler import build_verifier_task

        return build_verifier_task(
            original_task,
            original_result,
            verifier_node=verifier_node,
            coordinator_node=coordinator_node,
        )

    def close(self) -> None:
        """Shutdown all background discovery routines."""
        self.stop_discovery()
        global _active_peer_service
        with _active_peer_service_lock:
            if _active_peer_service is self:
                _active_peer_service = None

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
                        "has_outbound_token": bool(p.token),
                    }
                    for p in pinned
                ],
                "discovered_peers": [d.to_dict() for d in discovered],
            }
