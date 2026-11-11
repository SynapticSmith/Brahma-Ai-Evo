"""Mutual SAS Pairing, SPKI Pinning, and Token Issuance.

Implements ADR-032 and ADR-033:
- Out-of-band 6-digit Short Authentication String (SAS) pairing protocol.
- Ephemeral X25519 key agreement + HKDF-SHA256 transcript derivation.
- AES-256-GCM encrypted token delivery channel.
- Single-use 120s TTL nonce cache with replay protection.
- 3-attempt lockout (5 minutes) for brute-force mitigation.
- Peer registry with immutable SPKI pinning: NO TOFU AFTER FIRST PIN.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import json
import logging
from pathlib import Path
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from cryptography.hazmat.primitives.asymmetric import x25519

from core.peer.crypto import (
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    build_pairing_transcript,
    compute_node_id,
    compute_spki_sha256,
    derive_sas_and_session_key,
    ed25519_public_from_raw,
    ed25519_public_to_raw,
    generate_x25519_keypair,
    public_key_to_spki_der,
    x25519_public_from_raw,
    x25519_public_to_raw,
)
from core.peer.identity import NodeIdentity
from core.secret_store import load_secret_json, save_secret_json
from core.user_paths import get_user_data_dir

logger = logging.getLogger(__name__)

OFFER_TTL_S = 120.0
NONCE_CACHE_TTL_S = 600.0
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_DURATION_S = 300.0


class PairingError(Exception):
    """Base error for peer pairing."""


class PairingLockoutError(PairingError):
    """Raised when pairing attempts are temporarily locked out due to repeated failures."""


class NonceExpiredOrReplayedError(PairingError):
    """Raised when an offer nonce is expired, malformed, or already consumed."""


class FingerprintMismatchError(PairingError):
    """Raised when a peer presents a fingerprint differing from a pinned SPKI."""


class SasMismatchError(PairingError):
    """Raised when out-of-band SAS comparison fails."""


@dataclass
class PairingOffer:
    """Pairing offer details (rendered in UI or QR code)."""

    v: int
    node_id: str
    spki_sha256: str
    host: str
    port: int
    nonce: str
    created_at: float

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > OFFER_TTL_S

    def to_uri(self) -> str:
        fp_enc = urllib.parse.quote(self.spki_sha256, safe="")
        return f"brahma://pair?v={self.v}&node={self.node_id}&fp={fp_enc}&host={self.host}&port={self.port}&nonce={self.nonce}&ts={int(self.created_at)}"

    @classmethod
    def from_uri(cls, uri: str) -> PairingOffer:
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme != "brahma" or parsed.netloc != "pair":
            raise ValueError(f"Invalid brahma pairing URI: {uri}")
        params = urllib.parse.parse_qs(parsed.query)
        v = int(params.get("v", ["1"])[0])
        node_id = params.get("node", [""])[0]
        spki_sha256 = urllib.parse.unquote(params.get("fp", [""])[0])
        host = params.get("host", [""])[0]
        port = int(params.get("port", ["8000"])[0])
        nonce = params.get("nonce", [""])[0]
        ts_raw = params.get("ts", [""])[0]
        try:
            created_at = float(ts_raw) if ts_raw else time.time()
        except ValueError:
            created_at = time.time()

        if not (node_id and spki_sha256 and host and nonce):
            raise ValueError(f"Missing required pairing offer parameters in URI: {uri}")

        offer = cls(
            v=v,
            node_id=node_id,
            spki_sha256=spki_sha256,
            host=host,
            port=port,
            nonce=nonce,
            created_at=created_at,
        )
        if offer.is_expired:
            raise NonceExpiredOrReplayedError("Pairing offer has expired (exceeded 120s TTL)")
        return offer


@dataclass
class PinnedPeer:
    """Record of a successfully paired and pinned peer."""

    node_id: str
    name: str
    endpoint: str
    spki_sha256: str
    token: str
    profile: str
    paired_at: str
    static_pub_raw: Optional[str] = None
    status: str = "paired"


class NonceTracker:
    """In-memory cache tracking seen nonces with expiration."""

    def __init__(self) -> None:
        self._seen: Dict[str, float] = {}

    def check_and_record(self, nonce: str) -> bool:
        now = time.time()
        # Clean expired
        expired = [k for k, exp in self._seen.items() if exp < now]
        for k in expired:
            del self._seen[k]

        if nonce in self._seen:
            return False
        self._seen[nonce] = now + NONCE_CACHE_TTL_S
        return True


class LockoutTracker:
    """Tracks failed SAS attempts and enforces 5-minute lockout."""

    def __init__(self) -> None:
        self.failed_attempts: int = 0
        self.locked_until: float = 0.0

    def check_lockout(self) -> None:
        now = time.time()
        if now < self.locked_until:
            rem = int(self.locked_until - now)
            raise PairingLockoutError(f"Pairing locked due to repeated failures. Try again in {rem}s.")

    def record_failure(self) -> None:
        self.failed_attempts += 1
        if self.failed_attempts >= MAX_FAILED_ATTEMPTS:
            self.locked_until = time.time() + LOCKOUT_DURATION_S
            self.failed_attempts = 0
            logger.warning("Pairing locked out for 5 minutes after %d failed attempts", MAX_FAILED_ATTEMPTS)

    def record_success(self) -> None:
        self.failed_attempts = 0
        self.locked_until = 0.0


class PeerRegistry:
    """Manages trusted pinned peers stored under DPAPI user encryption."""

    def __init__(self, registry_file: Optional[Path | str] = None) -> None:
        self.file_path = Path(registry_file) if registry_file else (get_user_data_dir() / "peer" / "peers.json")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def load_peers(self) -> Dict[str, PinnedPeer]:
        """Load and decrypt pinned peers from storage."""
        if not self.file_path.is_file():
            return {}
        data = load_secret_json(self.file_path)
        peers_dict = data.get("peers", {})
        result = {}
        for nid, p_data in peers_dict.items():
            result[nid] = PinnedPeer(**p_data)
        return result

    def list_peers(self) -> List[PinnedPeer]:
        """List all trusted pinned peers."""
        return list(self.load_peers().values())

    def get_peer(self, node_id: str) -> Optional[PinnedPeer]:
        """Retrieve a pinned peer by node_id."""
        peers = self.load_peers()
        return peers.get(node_id)

    def save_peer(self, peer: PinnedPeer, allow_re_pair: bool = False) -> None:
        """Store or update a peer record. Enforces NO TOFU AFTER FIRST PIN."""
        peers = self.load_peers()
        existing = peers.get(peer.node_id)

        if existing and existing.spki_sha256 != peer.spki_sha256:
            if not allow_re_pair:
                raise FingerprintMismatchError(
                    f"Peer '{peer.node_id}' presented fingerprint '{peer.spki_sha256}' which differs "
                    f"from pinned fingerprint '{existing.spki_sha256}'. Potential interception or key rotation! "
                    "Refusing connection. Explicit re-pair required."
                )
            logger.info("Peer %s fingerprint rotated from %s to %s via explicit re-pair",
                        peer.node_id, existing.spki_sha256, peer.spki_sha256)

        peers[peer.node_id] = peer
        payload = {
            "version": 1,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "peers": {nid: asdict(p) for nid, p in peers.items()},
        }
        save_secret_json(self.file_path, payload, encrypt=True)

    def remove_peer(self, node_id: str) -> bool:
        """Revoke and remove a pinned peer."""
        peers = self.load_peers()
        if node_id in peers:
            del peers[node_id]
            payload = {
                "version": 1,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "peers": {nid: asdict(p) for nid, p in peers.items()},
            }
            save_secret_json(self.file_path, payload, encrypt=True)
            return True
        return False


class PairingCoordinator:
    """Coordinates ephemeral X25519 negotiation, SAS confirmation, and token wrapping."""

    def __init__(
        self,
        identity: NodeIdentity,
        registry: Optional[PeerRegistry] = None,
    ) -> None:
        self.identity = identity
        self.registry = registry or PeerRegistry()
        self.nonce_tracker = NonceTracker()
        self.lockout = LockoutTracker()

    def create_offer(self, host: str, port: int) -> PairingOffer:
        """Generate a fresh pairing offer with a single-use 120s nonce."""
        nonce = secrets.token_hex(16)
        return PairingOffer(
            v=1,
            node_id=self.identity.node_id,
            spki_sha256=self.identity.spki_sha256,
            host=host,
            port=port,
            nonce=nonce,
            created_at=time.time(),
        )

    def begin_exchange(
        self,
        offer: PairingOffer,
        is_initiator: bool,
    ) -> Tuple[x25519.X25519PrivateKey, bytes, bytes]:
        """Generate ephemeral X25519 keypair and export ephemeral and static public keys."""
        self.lockout.check_lockout()
        if offer.is_expired:
            raise NonceExpiredOrReplayedError("Pairing offer has expired (exceeded 120s TTL)")
        if not is_initiator:
            if not self.nonce_tracker.check_and_record(offer.nonce):
                raise NonceExpiredOrReplayedError(f"Pairing offer nonce '{offer.nonce}' already consumed or replayed")

        eph_priv, eph_pub = generate_x25519_keypair()
        eph_pub_bytes = x25519_public_to_raw(eph_pub)
        static_pub_bytes = ed25519_public_to_raw(self.identity.public_key)

        return eph_priv, eph_pub_bytes, static_pub_bytes

    def complete_key_exchange(
        self,
        eph_priv: x25519.X25519PrivateKey,
        peer_node_id: str,
        peer_eph_pub_bytes: bytes,
        peer_static_pub_bytes: bytes,
        peer_spki_sha256: str,
        nonce: str,
        is_initiator: bool,
    ) -> Tuple[str, bytes]:
        """Perform X25519 ECDH, build transcript, and calculate SAS + session key.

        Verifies peer's static public key matches claimed peer_node_id and peer_spki_sha256.
        """
        self.lockout.check_lockout()

        # 1. Verify peer static key integrity
        try:
            peer_static_pub = ed25519_public_from_raw(peer_static_pub_bytes)
            derived_node_id = compute_node_id(peer_static_pub)
            derived_spki_sha256 = compute_spki_sha256(public_key_to_spki_der(peer_static_pub))
        except Exception as exc:
            self.lockout.record_failure()
            raise PairingError(f"Malformed peer static public key: {exc}") from exc

        if derived_node_id != peer_node_id:
            self.lockout.record_failure()
            raise PairingError(f"Peer static public key maps to {derived_node_id}, not claimed {peer_node_id}")

        if derived_spki_sha256 != peer_spki_sha256:
            self.lockout.record_failure()
            raise FingerprintMismatchError(
                f"Peer static SPKI hash mismatch: got {derived_spki_sha256}, expected {peer_spki_sha256}"
            )

        # 2. X25519 ECDH
        try:
            peer_eph_pub = x25519_public_from_raw(peer_eph_pub_bytes)
            shared_secret = eph_priv.exchange(peer_eph_pub)
        except Exception as exc:
            self.lockout.record_failure()
            raise PairingError(f"Malformed peer ephemeral public key or exchange failed: {exc}") from exc

        # 3. Canonical transcript ordering: initiator first, callee second
        my_eph_bytes = x25519_public_to_raw(eph_priv.public_key())
        my_static_bytes = ed25519_public_to_raw(self.identity.public_key)

        if is_initiator:
            init_id, call_id = self.identity.node_id, peer_node_id
            init_eph, call_eph = my_eph_bytes, peer_eph_pub_bytes
            init_static, call_static = my_static_bytes, peer_static_pub_bytes
        else:
            init_id, call_id = peer_node_id, self.identity.node_id
            init_eph, call_eph = peer_eph_pub_bytes, my_eph_bytes
            init_static, call_static = peer_static_pub_bytes, my_static_bytes

        transcript = build_pairing_transcript(
            initiator_id=init_id,
            callee_id=call_id,
            initiator_eph_pub=init_eph,
            callee_eph_pub=call_eph,
            initiator_static_pub=init_static,
            callee_static_pub=call_static,
            nonce=nonce,
        )

        sas_code, session_key = derive_sas_and_session_key(shared_secret, transcript)
        return sas_code, session_key

    def wrap_token_payload(
        self,
        session_key: bytes,
        token: str,
        profile: str,
        callee_node_id: str,
        callee_spki_sha256: str,
        callee_endpoint: str,
    ) -> bytes:
        """Callee side: encrypt token delivery payload with session key using AES-256-GCM."""
        payload = {
            "token": token,
            "profile": profile,
            "callee_node_id": callee_node_id,
            "callee_spki_sha256": callee_spki_sha256,
            "callee_endpoint": callee_endpoint,
            "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        plain_bytes = json.dumps(payload).encode("utf-8")
        return aes_gcm_encrypt(session_key, plain_bytes)

    def unwrap_token_payload(
        self,
        session_key: bytes,
        encrypted_payload: bytes,
    ) -> Dict[str, Any]:
        """Initiator side: decrypt token delivery payload with session key."""
        try:
            plain_bytes = aes_gcm_decrypt(session_key, encrypted_payload)
            data = json.loads(plain_bytes.decode("utf-8"))
            self.lockout.record_success()
            return data
        except Exception as exc:
            self.lockout.record_failure()
            raise PairingError(f"Failed to decrypt pairing token payload: {exc}") from exc
