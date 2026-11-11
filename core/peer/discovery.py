"""Local Peer Discovery Protocol (ADR-034 & Deep Dive §4.2, §8.4).

Provides UDP broadcast beaconing and listening for nearby Brahma nodes on LAN:
    Port: 8444 (UDP)
    Service: _brahma-peer._tcp.local
Advisory only: discovery never auto-trusts peers (Anti-Pattern A-06).
Discovered nodes must be explicitly paired via 6-digit SAS before dispatch.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("brahma.peer.discovery")

DISCOVERY_PORT = 8444
DISCOVERY_MAGIC = "brahma_peer_announce_v1"


class DiscoveredPeer:
    """Represents a peer discovered on the local network."""

    def __init__(
        self,
        node_id: str,
        name: str,
        endpoint: str,
        spki_sha256: str,
        address: str,
        last_seen: float,
    ) -> None:
        self.node_id = node_id
        self.name = name
        self.endpoint = endpoint
        self.spki_sha256 = spki_sha256
        self.address = address
        self.last_seen = last_seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "endpoint": self.endpoint,
            "spki_sha256": self.spki_sha256,
            "address": self.address,
            "last_seen": self.last_seen,
        }


class PeerDiscoveryBeacon:
    """Broadcasts node identity periodically on LAN."""

    def __init__(
        self,
        node_id: str,
        name: str,
        endpoint: str,
        spki_sha256: str,
        port: int = DISCOVERY_PORT,
        target_address: str = "<broadcast>",
    ) -> None:
        self.node_id = node_id
        self.name = name
        self.endpoint = endpoint
        self.spki_sha256 = spki_sha256
        self.port = port
        self.target_address = target_address
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def broadcast_once(self) -> None:
        """Send a single UDP broadcast beacon."""
        payload = {
            "magic": DISCOVERY_MAGIC,
            "node_id": self.node_id,
            "name": self.name,
            "endpoint": self.endpoint,
            "spki_sha256": self.spki_sha256,
            "ts": time.time(),
        }
        data = json.dumps(payload).encode("utf-8")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(data, (self.target_address, self.port))
        except Exception as exc:
            logger.debug("Failed to send discovery beacon: %s", exc)

    def start(self, interval_s: float = 10.0) -> None:
        """Start background broadcast loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                self.broadcast_once()
                self._stop_event.wait(interval_s)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background broadcast loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)


class PeerDiscoveryListener:
    """Listens for UDP beacons from nearby Brahma nodes."""

    def __init__(self, port: int = DISCOVERY_PORT) -> None:
        self.port = port
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._discovered: Dict[str, DiscoveredPeer] = {}

    def start(self) -> None:
        """Start listening for beacons in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()

        def _listen() -> None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("", self.port))
                sock.settimeout(1.0)
            except Exception as exc:
                logger.warning("Could not bind discovery socket on port %d: %s", self.port, exc)
                return

            with sock:
                while not self._stop_event.is_set():
                    try:
                        data, addr = sock.recvfrom(4096)
                        parsed = json.loads(data.decode("utf-8"))
                        if parsed.get("magic") != DISCOVERY_MAGIC:
                            continue
                        nid = str(parsed.get("node_id") or "")
                        if not nid:
                            continue
                        peer = DiscoveredPeer(
                            node_id=nid,
                            name=str(parsed.get("name") or "Brahma Node"),
                            endpoint=str(parsed.get("endpoint") or f"https://{addr[0]}:8443"),
                            spki_sha256=str(parsed.get("spki_sha256") or ""),
                            address=addr[0],
                            last_seen=time.time(),
                        )
                        with self._lock:
                            self._discovered[nid] = peer
                    except socket.timeout:
                        continue
                    except Exception:
                        continue

        self._thread = threading.Thread(target=_listen, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def list_discovered(self, max_age_s: float = 60.0) -> List[DiscoveredPeer]:
        """Return list of active discovered peers."""
        now = time.time()
        with self._lock:
            # Prune stale
            fresh = [p for p in self._discovered.values() if (now - p.last_seen) <= max_age_s]
            return sorted(fresh, key=lambda p: p.name)
